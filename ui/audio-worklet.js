/* AudioWorklet: downsample microphone input to 16 kHz mono PCM16 and post 1024-sample frames (64 ms). */
class NecroMicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.out = new Int16Array(1024);
    this.n = 0;
  }
  process(inputs) {
    var ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    while (this.pos < ch.length) {
      var s = Math.max(-1, Math.min(1, ch[Math.floor(this.pos)]));
      this.out[this.n++] = s < 0 ? s * 32768 : s * 32767;
      if (this.n === this.out.length) {
        var copy = this.out.slice();
        this.port.postMessage(copy.buffer, [copy.buffer]);
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor("necro-mic", NecroMicProcessor);
