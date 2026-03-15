/**
 * whisper.js — Transcription locale via Transformers.js (WASM)
 * Supporte : auto-détection de langue, traduction en anglais si non-français
 */
let _pipeline    = null;
let _currentModel = null;

const TRANSFORMERS_URL = 'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2/dist/transformers.min.js';

export async function transcribe(audioBlob, { model, language, onProgress, onLangDetected } = {}) {
  model = model || 'Xenova/whisper-base';

  if (!_pipeline || _currentModel !== model) {
    const { pipeline } = await import(TRANSFORMERS_URL);
    onProgress?.('Chargement du modèle…', 5);
    _pipeline = await pipeline('automatic-speech-recognition', model, {
      progress_callback: (p) => {
        if (p.status === 'downloading') {
          const pct = Math.round((p.loaded / (p.total || 1)) * 60); // 0-60%
          onProgress?.(`Téléchargement modèle : ${pct}%`, pct);
        } else if (p.status === 'loaded') {
          onProgress?.('Modèle chargé.', 65);
        }
      },
    });
    _currentModel = model;
  }

  const arrayBuffer = await audioBlob.arrayBuffer();
  onProgress?.('Décodage audio…', 68);
  const float32 = await _decodeAudio(arrayBuffer);

  // ── Étape 1 : détection de langue (rapide, beam_size=1) si auto ──────────
  let task     = 'transcribe';
  let forcedLang = language === 'auto' ? undefined : language;
  let detectedLang = forcedLang || null;

  if (language === 'auto' || !language) {
    onProgress?.('Détection de la langue…', 70);
    try {
      const probe = await _pipeline(float32, {
        task:       'transcribe',
        language:   undefined,
        chunk_length_s: 15,
        stride_length_s: 3,
        return_timestamps: false,
        num_beams: 1,
        max_new_tokens: 20,
      });
      // The pipeline may expose language info via pipeline.tokenizer or result
      // Fallback: try to detect via forced alignment metadata if available
      const detected = probe?.language || _pipeline.tokenizer?.language || null;
      if (detected) {
        detectedLang = detected;
        onLangDetected?.(detected);
        // If not French, translate to English
        if (detected !== 'fr' && detected !== 'french') {
          task = 'translate';
          onProgress?.(`Langue détectée : ${detected} → traduction en anglais`, 74);
        } else {
          onProgress?.(`Langue détectée : français`, 74);
        }
      }
    } catch (_) {
      // Ignore detection failure, proceed with transcribe
    }
  } else {
    detectedLang = forcedLang;
    onLangDetected?.(forcedLang);
    // Explicit non-French → translate
    if (forcedLang && forcedLang !== 'fr') {
      task = 'translate';
    }
  }

  // ── Étape 2 : transcription / traduction complète ─────────────────────────
  onProgress?.('Transcription en cours…', 78);

  const options = {
    task,
    chunk_length_s:  30,
    stride_length_s:  5,
    return_timestamps: false,
  };
  if (forcedLang) options.language = forcedLang;

  const result = await _pipeline(float32, options);
  onProgress?.('Terminé.', 100);

  return Array.isArray(result) ? result.map((r) => r.text).join('\n') : result.text;
}

async function _decodeAudio(arrayBuffer) {
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  const decoded  = await audioCtx.decodeAudioData(arrayBuffer);
  audioCtx.close();
  // Mix to mono
  const ch   = decoded.numberOfChannels;
  const len  = decoded.length;
  const mono = new Float32Array(len);
  for (let c = 0; c < ch; c++) {
    const chanData = decoded.getChannelData(c);
    for (let i = 0; i < len; i++) mono[i] += chanData[i];
  }
  for (let i = 0; i < len; i++) mono[i] /= ch;
  return mono;
}
