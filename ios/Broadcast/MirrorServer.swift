import Foundation
import Network

/// Single-client TCP server. Wire format: [type u8][length u32 LE][payload] (see protocol.py).
final class MirrorServer {
    static let msgHello: UInt8 = 0x01
    static let msgVideo: UInt8 = 0x02
    static let msgTouch: UInt8 = 0x10
    static let msgKeyframeRequest: UInt8 = 0x11
    static let protocolVersion: UInt16 = 1
    private static let maxPayload = 1 << 20          // inbound messages are tiny
    private static let maxBacklogBytes = 1_500_000   // drop video when the link can't keep up

    var onTouch: ((UInt8, UInt8, Float, Float) -> Void)?
    var onKeyframeRequest: (() -> Void)?
    var onClientChanged: ((Bool) -> Void)?

    private let port: NWEndpoint.Port
    private let queue = DispatchQueue(label: "phonemirror.server")
    private var listener: NWListener?
    private var client: NWConnection?
    private var inbox = Data()
    private var backlog = 0
    private var waitingForKeyframe = false
    private let lock = NSLock()
    private var _hasClient = false

    /// Thread-safe, read from the video thread.
    var hasClient: Bool { lock.lock(); defer { lock.unlock() }; return _hasClient }

    init(port: UInt16) { self.port = NWEndpoint.Port(rawValue: port)! }

    func start() {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        let params = NWParameters(tls: nil, tcp: tcp)
        params.allowLocalEndpointReuse = true
        guard let l = try? NWListener(using: params, on: port) else { return }
        l.newConnectionHandler = { [weak self] conn in self?.accept(conn) }
        l.start(queue: queue)
        listener = l
    }

    func stop() {
        queue.async {
            self.client?.cancel()
            self.client = nil
            self.listener?.cancel()
            self.listener = nil
            self.setHasClient(false)
        }
    }

    private func setHasClient(_ value: Bool) {
        lock.lock(); _hasClient = value; lock.unlock()
        onClientChanged?(value)
    }

    private func accept(_ conn: NWConnection) {
        client?.cancel()                 // newest connection wins
        client = conn
        inbox.removeAll()
        backlog = 0
        waitingForKeyframe = false
        conn.stateUpdateHandler = { [weak self, weak conn] state in
            guard let self = self, let conn = conn, self.client === conn else { return }
            switch state {
            case .ready:
                self.setHasClient(true)
                var v = MirrorServer.protocolVersion.littleEndian
                self.rawSend(type: MirrorServer.msgHello, payload: Data(bytes: &v, count: 2))
                self.receive(on: conn)
            case .failed, .cancelled:
                self.client = nil
                self.setHasClient(false)
            default: break
            }
        }
        conn.start(queue: queue)
    }

    // MARK: receiving

    private func receive(on conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 4096) { [weak self, weak conn] data, _, isComplete, error in
            guard let self = self, let conn = conn, self.client === conn else { return }
            if let data = data, !data.isEmpty {
                self.inbox.append(data)
                if !self.parseInbox() { conn.cancel(); return }
            }
            if isComplete || error != nil { conn.cancel(); return }
            self.receive(on: conn)
        }
    }

    /// Returns false on a protocol violation (caller drops the connection).
    private func parseInbox() -> Bool {
        while inbox.count >= 5 {
            let type = inbox[inbox.startIndex]
            let length = Int(UInt32(inbox[inbox.startIndex + 1]) | UInt32(inbox[inbox.startIndex + 2]) << 8 |
                             UInt32(inbox[inbox.startIndex + 3]) << 16 | UInt32(inbox[inbox.startIndex + 4]) << 24)
            if length > MirrorServer.maxPayload { return false }
            if inbox.count < 5 + length { break }
            let payload = Data(inbox.subdata(in: (inbox.startIndex + 5)..<(inbox.startIndex + 5 + length)))
            inbox = Data(inbox.subdata(in: (inbox.startIndex + 5 + length)..<inbox.endIndex))
            handle(type: type, payload: payload)
        }
        return true
    }

    private func handle(type: UInt8, payload: Data) {
        switch type {
        case MirrorServer.msgTouch:
            guard payload.count == 10 else { return }
            let action = payload[payload.startIndex]
            let finger = payload[payload.startIndex + 1]
            let x = Float(bitPattern: readU32(payload, at: 2))
            let y = Float(bitPattern: readU32(payload, at: 6))
            onTouch?(action, finger, x, y)
        case MirrorServer.msgKeyframeRequest:
            onKeyframeRequest?()
        default:
            break
        }
    }

    private func readU32(_ d: Data, at offset: Int) -> UInt32 {
        let i = d.startIndex + offset
        return UInt32(d[i]) | UInt32(d[i + 1]) << 8 | UInt32(d[i + 2]) << 16 | UInt32(d[i + 3]) << 24
    }

    // MARK: sending

    /// Called from the encoder thread. Drops delta frames while the link is congested and asks for a keyframe after.
    func sendVideo(_ annexB: Data, keyframe: Bool, orientation: UInt8) {
        queue.async {
            guard let conn = self.client else { return }
            if self.backlog > MirrorServer.maxBacklogBytes {
                self.waitingForKeyframe = true
                if !keyframe { return }
            }
            if self.waitingForKeyframe && !keyframe {
                self.onKeyframeRequest?()
                return
            }
            if keyframe { self.waitingForKeyframe = false }
            var payload = Data([orientation, keyframe ? 1 : 0])
            payload.append(annexB)
            self.rawSend(type: MirrorServer.msgVideo, payload: payload, on: conn)
        }
    }

    private func rawSend(type: UInt8, payload: Data, on conn: NWConnection? = nil) {
        guard let conn = conn ?? client else { return }
        var frame = Data([type])
        var len = UInt32(payload.count).littleEndian
        frame.append(Data(bytes: &len, count: 4))
        frame.append(payload)
        backlog += frame.count
        conn.send(content: frame, completion: .contentProcessed { [weak self] _ in
            self?.queue.async { self?.backlog -= frame.count }
        })
    }
}
