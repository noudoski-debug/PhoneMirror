import ReplayKit
import CoreMedia

/// Runs for as long as the broadcast is active: streams the screen out and injects incoming touches.
class SampleHandler: RPBroadcastSampleHandler {
    private let server = MirrorServer(port: 8765)
    private let encoder = VideoEncoder()
    private let injector = TouchInjector()

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        encoder.onFrame = { [weak self] data, key, orientation in
            self?.server.sendVideo(data, keyframe: key, orientation: orientation)
        }
        server.onTouch = { [weak self] action, finger, x, y in self?.injector.handle(action: action, finger: finger, x: x, y: y) }
        server.onKeyframeRequest = { [weak self] in self?.encoder.requestKeyframe() }
        server.onClientChanged = { [weak self] connected in
            if connected { self?.encoder.requestKeyframe() } else { self?.injector.releaseAll() }
        }
        server.start()
    }

    override func broadcastFinished() {
        injector.releaseAll()
        server.stop()
        encoder.invalidate()
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video, server.hasClient else { return }
        var orientation: UInt8 = 1
        if let n = CMGetAttachment(sampleBuffer, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber {
            orientation = n.uint8Value
        }
        encoder.encode(sampleBuffer, orientation: orientation)
    }
}
