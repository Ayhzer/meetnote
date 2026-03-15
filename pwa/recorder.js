/**
 * recorder.js — MediaRecorder + Wake Lock + AnalyserNode (VU-meter)
 */
export class AudioRecorder {
  constructor() {
    this._mediaRecorder = null;
    this._chunks        = [];
    this._wakeLock      = null;
    this._stream        = null;
    this._analyser      = null;
    this._analyserData  = null;
    this._audioCtx      = null;
  }

  async start() {
    this._chunks = [];
    this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this._mediaRecorder = new MediaRecorder(this._stream, {
      mimeType:    this._getSupportedMimeType(),
      audioBitsPerSecond: 24000,   // 24 kbps → ~10.8 Mo/h → 2h ≈ 21.6 Mo
    });
    this._mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) this._chunks.push(e.data);
    };
    this._mediaRecorder.start(1000);

    // AnalyserNode for VU-meter
    try {
      this._audioCtx     = new (window.AudioContext || window.webkitAudioContext)();
      const source       = this._audioCtx.createMediaStreamSource(this._stream);
      this._analyser     = this._audioCtx.createAnalyser();
      this._analyser.fftSize = 256;
      this._analyserData = new Uint8Array(this._analyser.frequencyBinCount);
      source.connect(this._analyser);
    } catch (_) {
      this._analyser = null;
    }

    // Wake Lock — empêche le verrouillage écran sur mobile
    if ('wakeLock' in navigator) {
      try {
        this._wakeLock = await navigator.wakeLock.request('screen');
      } catch (_) {}
    }
  }

  /** Returns RMS level 0..1 for VU-meter */
  getLevel() {
    if (!this._analyser) return 0;
    this._analyser.getByteTimeDomainData(this._analyserData);
    let sum = 0;
    for (let i = 0; i < this._analyserData.length; i++) {
      const v = (this._analyserData[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / this._analyserData.length);
  }

  async stop() {
    return new Promise((resolve) => {
      this._mediaRecorder.onstop = () => {
        const blob = new Blob(this._chunks, { type: this._mediaRecorder.mimeType });
        resolve(blob);
      };
      this._mediaRecorder.stop();
      this._stream?.getTracks().forEach((t) => t.stop());

      if (this._analyser) {
        try { this._audioCtx?.close(); } catch (_) {}
        this._analyser    = null;
        this._analyserData = null;
      }

      if (this._wakeLock) {
        this._wakeLock.release().catch(() => {});
        this._wakeLock = null;
      }
    });
  }

  _getSupportedMimeType() {
    const types = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
    ];
    return types.find((t) => MediaRecorder.isTypeSupported(t)) || '';
  }
}
