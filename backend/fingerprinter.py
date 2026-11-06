import sys
import acoustid

def generate_fingerprint(file_path):
    print(f"Analyzing audio waveform for: {file_path}...")
    try:
        # This calls the C++ chromaprint (fpcalc) engine under the hood
        duration, fingerprint = acoustid.fingerprint_file(file_path)
        
        print("\n--- 🟢 Audio Fingerprint Success ---")
        print(f"Duration: {duration} seconds")
        # The fingerprint is massive, so we just print the first 60 characters to verify it worked
        print(f"Raw C++ Fingerprint Hash: {fingerprint.decode('utf-8')[:60]}...")
        
        return duration, fingerprint
    except acoustid.NoBackendError:
        print("\n--- 🔴 Error: Chromaprint (fpcalc) not found in system path ---")
    except Exception as e:
        print(f"\n--- 🔴 Error analyzing file: {e} ---")

if __name__ == "__main__":
    # This allows us to run the script directly from the terminal
    if len(sys.argv) < 2:
        print("Usage: python fingerprinter.py <path_to_audio_file>")
        sys.exit(1)
        
    target_file = sys.argv[1]
    generate_fingerprint(target_file)
