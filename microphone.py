import sounddevice
from scipy.io.wavfile import write

fs = 44100 

second = int(input('Enter the record time'))

print("Recording...")
record_voice = sounddevice.rec(int(second * fs), samplerate=fs, channels=2)

sounddevice.wait()
print("Recording finished!")

write("output.wav", fs, record_voice)
print("Audio saved as output.wav")
