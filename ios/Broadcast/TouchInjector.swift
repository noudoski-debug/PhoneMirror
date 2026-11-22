import Foundation

/// Keeps the set of fingers currently down and forwards them to the HID bridge.
final class TouchInjector {
    enum Action: UInt8 { case down = 0, move = 1, up = 2 }
    private var fingers: [UInt8: (x: Float, y: Float)] = [:]
    private let maxFingers = 10

    var isAvailable: Bool { HIDBridge.isAvailable() }

    func handle(action: UInt8, finger: UInt8, x: Float, y: Float) {
        guard let action = Action(rawValue: action), x.isFinite, y.isFinite else { return }
        switch action {
        case .down:
            guard fingers[finger] != nil || fingers.count < maxFingers else { return }
            fingers[finger] = (x, y)
            send(isMove: false)
        case .move:
            guard fingers[finger] != nil else { return }
            fingers[finger] = (x, y)
            send(isMove: true)
        case .up:
            guard fingers[finger] != nil else { return }
            fingers[finger] = (x, y)
            send(isMove: false, lifting: finger)
            fingers[finger] = nil
        }
    }

    func releaseAll() {
        guard !fingers.isEmpty else { return }
        let touches: [[AnyHashable: Any]] = fingers.map {
            ["id": NSNumber(value: $0.key), "x": NSNumber(value: $0.value.x),
             "y": NSNumber(value: $0.value.y), "down": NSNumber(value: false)]
        }
        fingers.removeAll()
        HIDBridge.sendTouches(touches, isMove: false)
    }

    private func send(isMove: Bool, lifting: UInt8? = nil) {
        let touches: [[AnyHashable: Any]] = fingers.map { id, p in
            ["id": NSNumber(value: id), "x": NSNumber(value: p.x), "y": NSNumber(value: p.y),
             "down": NSNumber(value: id != lifting)]
        }
        HIDBridge.sendTouches(touches, isMove: isMove)
    }
}
