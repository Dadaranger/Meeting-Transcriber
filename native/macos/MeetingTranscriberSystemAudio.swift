import AudioToolbox
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
private final class SystemAudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    private let output = FileHandle.standardOutput
    private let errorOutput = FileHandle.standardError
    private var stream: SCStream?

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: false
        )
        guard let display = content.displays.first else {
            throw CaptureFailure("No active display is available for system-audio capture")
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 2
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        configuration.showsCursor = false

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        try stream.addStreamOutput(
            self,
            type: .audio,
            sampleHandlerQueue: DispatchQueue(label: "meeting-transcriber.system-audio")
        )
        self.stream = stream
        try await stream.startCapture()
        errorOutput.write(Data("READY\n".utf8))
    }

    func stop() async {
        guard let stream else { return }
        try? await stream.stopCapture()
        self.stream = nil
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .audio, sampleBuffer.isValid else { return }
        do {
            let pcm = try pcm16Data(from: sampleBuffer)
            if !pcm.isEmpty {
                output.write(pcm)
            }
        } catch {
            errorOutput.write(Data("Audio conversion failed: \(error.localizedDescription)\n".utf8))
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        errorOutput.write(Data("Capture stopped: \(error.localizedDescription)\n".utf8))
        Darwin.exit(1)
    }

    private func pcm16Data(from sampleBuffer: CMSampleBuffer) throws -> Data {
        guard
            let description = CMSampleBufferGetFormatDescription(sampleBuffer),
            let format = CMAudioFormatDescriptionGetStreamBasicDescription(description)?.pointee,
            format.mFormatID == kAudioFormatLinearPCM
        else {
            throw CaptureFailure("ScreenCaptureKit returned an unsupported audio format")
        }

        let channels = Int(format.mChannelsPerFrame)
        let frames = CMSampleBufferGetNumSamples(sampleBuffer)
        guard channels > 0, frames > 0 else { return Data() }

        let byteCount = MemoryLayout<AudioBufferList>.size
            + max(0, channels - 1) * MemoryLayout<AudioBuffer>.stride
        let storage = UnsafeMutableRawPointer.allocate(
            byteCount: byteCount,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { storage.deallocate() }
        let listPointer = storage.bindMemory(to: AudioBufferList.self, capacity: 1)
        var retainedBlockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: listPointer,
            bufferListSize: byteCount,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
            blockBufferOut: &retainedBlockBuffer
        )
        guard status == noErr else {
            throw CaptureFailure("Could not read the ScreenCaptureKit audio buffer (\(status))")
        }

        let buffers = UnsafeMutableAudioBufferListPointer(listPointer)
        let nonInterleaved = format.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
        let isFloat = format.mFormatFlags & kAudioFormatFlagIsFloat != 0
        let isSignedInteger = format.mFormatFlags & kAudioFormatFlagIsSignedInteger != 0
        var result = [Int16](repeating: 0, count: frames * channels)

        if isFloat && format.mBitsPerChannel == 32 {
            copyFloat32(
                buffers: buffers,
                frames: frames,
                channels: channels,
                nonInterleaved: nonInterleaved,
                destination: &result
            )
        } else if isSignedInteger && format.mBitsPerChannel == 16 {
            copyInt16(
                buffers: buffers,
                frames: frames,
                channels: channels,
                nonInterleaved: nonInterleaved,
                destination: &result
            )
        } else {
            throw CaptureFailure(
                "Unsupported PCM layout: \(format.mBitsPerChannel)-bit flags \(format.mFormatFlags)"
            )
        }
        return result.withUnsafeBytes { Data($0) }
    }

    private func copyFloat32(
        buffers: UnsafeMutableAudioBufferListPointer,
        frames: Int,
        channels: Int,
        nonInterleaved: Bool,
        destination: inout [Int16]
    ) {
        for frame in 0..<frames {
            for channel in 0..<channels {
                let bufferIndex = nonInterleaved ? channel : 0
                let sampleIndex = nonInterleaved ? frame : frame * channels + channel
                guard
                    bufferIndex < buffers.count,
                    let data = buffers[bufferIndex].mData
                else { continue }
                let value = data.assumingMemoryBound(to: Float.self)[sampleIndex]
                let scaled = max(-1.0, min(1.0, value)) * Float(Int16.max)
                destination[frame * channels + channel] = Int16(scaled.rounded())
            }
        }
    }

    private func copyInt16(
        buffers: UnsafeMutableAudioBufferListPointer,
        frames: Int,
        channels: Int,
        nonInterleaved: Bool,
        destination: inout [Int16]
    ) {
        for frame in 0..<frames {
            for channel in 0..<channels {
                let bufferIndex = nonInterleaved ? channel : 0
                let sampleIndex = nonInterleaved ? frame : frame * channels + channel
                guard
                    bufferIndex < buffers.count,
                    let data = buffers[bufferIndex].mData
                else { continue }
                destination[frame * channels + channel] =
                    data.assumingMemoryBound(to: Int16.self)[sampleIndex]
            }
        }
    }
}

private struct CaptureFailure: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? { message }
}

@main
private struct MeetingTranscriberSystemAudio {
    static func main() {
        guard #available(macOS 13.0, *) else {
            FileHandle.standardError.write(Data("macOS 13 or newer is required\n".utf8))
            Darwin.exit(1)
        }

        let output = SystemAudioOutput()
        signal(SIGTERM, SIG_IGN)
        signal(SIGINT, SIG_IGN)
        let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        let interruption = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        let stop: () -> Void = {
            Task {
                await output.stop()
                Darwin.exit(0)
            }
        }
        termination.setEventHandler(handler: stop)
        interruption.setEventHandler(handler: stop)
        termination.resume()
        interruption.resume()

        Task {
            do {
                try await output.start()
            } catch {
                FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
                Darwin.exit(1)
            }
        }
        dispatchMain()
    }
}
