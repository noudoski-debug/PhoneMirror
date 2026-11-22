import Foundation
import CoreMedia
import CoreVideo
import VideoToolbox

/// Downscales ReplayKit frames and encodes them to H.264 (Annex B), hardware accelerated.
final class VideoEncoder {
    var onFrame: ((Data, Bool, UInt8) -> Void)?

    private let maxLongSide = 960
    private var session: VTCompressionSession?
    private var transfer: VTPixelTransferSession?
    private var pool: CVPixelBufferPool?
    private var srcSize = (w: 0, h: 0)
    private let stateLock = NSLock()
    private var _forceKeyframe = true
    private var forceKeyframe: Bool {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _forceKeyframe }
        set { stateLock.lock(); _forceKeyframe = newValue; stateLock.unlock() }
    }
    private var orientation: UInt8 = 1
    private let startCode = Data([0, 0, 0, 1])

    func requestKeyframe() { forceKeyframe = true }

    func encode(_ sampleBuffer: CMSampleBuffer, orientation: UInt8) {
        guard let src = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let w = CVPixelBufferGetWidth(src), h = CVPixelBufferGetHeight(src)
        if session == nil || srcSize != (w, h) {
            guard configure(width: w, height: h) else { return }
        }
        guard let session = session, let transfer = transfer, let pool = pool else { return }

        var dstOpt: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(nil, pool, &dstOpt) == kCVReturnSuccess, let dst = dstOpt else { return }
        guard VTPixelTransferSessionTransferImage(transfer, from: src, to: dst) == noErr else { return }

        self.orientation = orientation
        let props: CFDictionary? = forceKeyframe ? [kVTEncodeFrameOptionKey_ForceKeyFrame: true] as CFDictionary : nil
        forceKeyframe = false
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        VTCompressionSessionEncodeFrame(session, imageBuffer: dst, presentationTimeStamp: pts, duration: .invalid,
                                        frameProperties: props, infoFlagsOut: nil) { [weak self] status, _, sb in
            guard status == noErr, let sb = sb else { return }
            self?.emit(sb)
        }
    }

    func invalidate() {
        if let s = session { VTCompressionSessionInvalidate(s) }
        if let t = transfer { VTPixelTransferSessionInvalidate(t) }
        session = nil; transfer = nil; pool = nil
    }

    // MARK: setup

    private func configure(width: Int, height: Int) -> Bool {
        invalidate()
        let scale = min(1.0, Double(maxLongSide) / Double(max(width, height)))
        let outW = max(16, Int(Double(width) * scale) & ~1)
        let outH = max(16, Int(Double(height) * scale) & ~1)

        var s: VTCompressionSession?
        guard VTCompressionSessionCreate(allocator: nil, width: Int32(outW), height: Int32(outH),
                                         codecType: kCMVideoCodecType_H264, encoderSpecification: nil,
                                         imageBufferAttributes: nil, compressedDataAllocator: nil,
                                         outputCallback: nil, refcon: nil, compressionSessionOut: &s) == noErr,
              let session = s else { return false }
        func set(_ key: CFString, _ value: CFTypeRef) { VTSessionSetProperty(session, key: key, value: value) }
        set(kVTCompressionPropertyKey_RealTime, kCFBooleanTrue)
        set(kVTCompressionPropertyKey_ProfileLevel, kVTProfileLevel_H264_Baseline_AutoLevel)
        set(kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse)
        set(kVTCompressionPropertyKey_MaxFrameDelayCount, 0 as CFNumber)
        set(kVTCompressionPropertyKey_MaxKeyFrameInterval, 60 as CFNumber)
        set(kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, 1 as CFNumber)
        set(kVTCompressionPropertyKey_ExpectedFrameRate, 60 as CFNumber)
        set(kVTCompressionPropertyKey_AverageBitRate, 10_000_000 as CFNumber)
        VTCompressionSessionPrepareToEncodeFrames(session)

        var t: VTPixelTransferSession?
        guard VTPixelTransferSessionCreate(allocator: nil, pixelTransferSessionOut: &t) == noErr, let transfer = t else {
            VTCompressionSessionInvalidate(session); return false
        }
        let attrs: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
            kCVPixelBufferWidthKey as String: outW,
            kCVPixelBufferHeightKey as String: outH,
            kCVPixelBufferIOSurfacePropertiesKey as String: [:] as [String: Any],
        ]
        var p: CVPixelBufferPool?
        guard CVPixelBufferPoolCreate(nil, [kCVPixelBufferPoolMinimumBufferCountKey: 3] as CFDictionary,
                                      attrs as CFDictionary, &p) == kCVReturnSuccess, let pool = p else {
            VTCompressionSessionInvalidate(session); VTPixelTransferSessionInvalidate(transfer); return false
        }
        self.session = session; self.transfer = transfer; self.pool = pool
        srcSize = (width, height)
        forceKeyframe = true
        return true
    }

    // MARK: output (AVCC -> Annex B)

    private func emit(_ sb: CMSampleBuffer) {
        guard CMSampleBufferDataIsReady(sb), let block = CMSampleBufferGetDataBuffer(sb) else { return }
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: false) as? [[CFString: Any]]
        let notSync = (attachments?.first?[kCMSampleAttachmentKey_NotSync] as? Bool) ?? false
        let isKey = !notSync

        var out = Data()
        if isKey, let fd = CMSampleBufferGetFormatDescription(sb) {
            var count = 0
            CMVideoFormatDescriptionGetH264ParameterSetAtIndex(fd, parameterSetIndex: 0, parameterSetPointerOut: nil,
                                                               parameterSetSizeOut: nil, parameterSetCountOut: &count,
                                                               nalUnitHeaderLengthOut: nil)
            for i in 0..<count {
                var ptr: UnsafePointer<UInt8>?
                var size = 0
                if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(fd, parameterSetIndex: i, parameterSetPointerOut: &ptr,
                                                                      parameterSetSizeOut: &size, parameterSetCountOut: nil,
                                                                      nalUnitHeaderLengthOut: nil) == noErr, let ptr = ptr {
                    out.append(startCode)
                    out.append(ptr, count: size)
                }
            }
        }

        var length = 0
        var base: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length,
                                          dataPointerOut: &base) == kCMBlockBufferNoErr, let base = base else { return }
        let bytes = UnsafeRawPointer(base).assumingMemoryBound(to: UInt8.self)
        var offset = 0
        while offset + 4 <= length {   // 4-byte big-endian NAL length prefixes
            let n = Int(bytes[offset]) << 24 | Int(bytes[offset + 1]) << 16 | Int(bytes[offset + 2]) << 8 | Int(bytes[offset + 3])
            offset += 4
            guard n > 0, offset + n <= length else { break }
            out.append(startCode)
            out.append(bytes + offset, count: n)
            offset += n
        }
        onFrame?(out, isKey, orientation)
    }
}
