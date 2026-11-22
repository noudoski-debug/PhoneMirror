import SwiftUI
import ReplayKit

@main
struct PhoneMirrorApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

/// System broadcast button: tapping it opens the iOS broadcast sheet preselected on our extension.
struct BroadcastPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 96, height: 96))
        picker.preferredExtension = "com.phonemirror.app.broadcast"
        picker.showsMicrophoneButton = false
        return picker
    }
    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}

struct ContentView: View {
    var body: some View {
        VStack(spacing: 24) {
            Text("PhoneMirror").font(.largeTitle).bold()
            Text("1. Connect the iPhone to the PC with a USB cable and tap \"Trust\".\n2. Start the PhoneMirror program on the PC.\n3. Tap the button below, choose PhoneMirror Broadcast and press Start Broadcast.\n4. Switch to your game: the screen appears on the PC and keys control it.")
                .font(.callout)
                .multilineTextAlignment(.leading)
            BroadcastPicker().frame(width: 96, height: 96)
            Text("To stop, open Control Center, tap the red status bar and Stop Broadcast.")
                .font(.footnote).foregroundColor(.secondary)
        }
        .padding(24)
    }
}
