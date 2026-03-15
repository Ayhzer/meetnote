/**
 * recorder.js — MediaRecorder + Wake Lock API
 */
export class AudioRecorder {
  constructor() {
    this._mediaRecorder = null;
    this._chunks = [];
    this._wakeLock = null;
    this._stream = null;
  }

  async start() {
    this._chunks = [];
    this._stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this._mediaRecorder = new MediaRecorder(this._stream, {
      mimeType: this._getSupportedMimeType(),
    });
    this._mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) this._chunks.push(e.data);
    };
    this._mediaRecorder.start(1000); // collecte par tranches de 1s

    // Wake Lock — empêche le verrouillage écran sur mobile
    if ('wakeLock' in navigator) {
      try {
        this._wakeLock = await navigator.wakeLock.request('screen');
      } catch (_) {
        // Wake Lock non disponible — pas bloquant
      }
    }
  }

  async stop() {
    return new Promise((resolve) => {
      this._mediaRecorder.onstop = () => {
        const blob = new Blob(this._chunks, { type: this._mediaRecorder.mimeType });
        resolve(blob);
      };
      this._mediaRecorder.stop();
      this._stream?.getTracks().forEach((t) => t.stop());

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
