// MediaRecorder supplies a continuous container split across blobs. Preserve byte order.
export const CHUNK_BYTES = 1024 * 1024;
const MAX_PENDING_BYTES = 32 * CHUNK_BYTES;

export async function request(path, data, binary = false) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(path, {
        method: 'POST', signal: controller.signal,
        headers: {'Content-Type': binary ? 'application/octet-stream' : 'application/json'},
        body: binary ? data : JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok) {
        const error = new Error(result.error || `Recorder returned ${response.status}.`);
        error.permanent = response.status >= 400 && response.status < 500;
        throw error;
      }
      return result;
    } catch (error) {
      lastError = error;
      if (error.permanent) break;
    } finally { clearTimeout(timeout); }
    if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw lastError;
}

export class WebcamCapture {
  constructor({send = request, Recorder = globalThis.MediaRecorder, changed = () => {},
               now = () => performance.now(), newId = () => crypto.randomUUID().replaceAll('-', '')} = {}) {
    Object.assign(this, {send, Recorder, changed, now, newId});
    this.phase = 'idle';
    this.queue = [];
    this.message = '';
    this.pendingBytes = 0;
  }

  get busy() { return !['idle', 'saved'].includes(this.phase); }
  get elapsed() { return this.startedAt == null ? 0 : ((this.endedAt ?? this.now()) - this.startedAt) / 1000; }

  async start(stream) {
    if (this.busy) throw new Error('Stop and save the current video first.');
    this.phase = 'starting';
    this.message = 'Preparing video file…';
    this.id = null;
    this.changed();
    let allocated = false;
    try {
      const types = stream.getAudioTracks().length
        ? ['video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4']
        : ['video/webm;codecs=vp8', 'video/webm', 'video/mp4'];
      const mimeType = types.find(type => this.Recorder.isTypeSupported(type));
      if (!mimeType) throw new Error('This browser cannot record WebM or MP4. Try another browser.');
      this.recorder = new this.Recorder(stream, {mimeType, videoBitsPerSecond: 5000000});
      this.id = this.newId();
      this.queue = [];
      this.sequence = this.bytes = this.pendingBytes = 0;
      this.failed = this.uploading = this.ended = false;
      this.startedAt = this.endedAt = null;
      this.warning = '';
      const track = stream.getVideoTracks()[0];
      const settings = track.getSettings();
      this.file = await this.send('/api/video/start', {
        id: this.id, mime_type: this.recorder.mimeType || mimeType,
        capture: {camera: track.label, width: settings.width, height: settings.height,
                  frame_rate: settings.frameRate, audio: stream.getAudioTracks().length > 0},
      });
      allocated = true;
      this.recorder.ondataavailable = event => this.enqueue(event.data);
      this.recorder.onerror = event => { this.warning = event.error?.message || 'Camera recording error.'; };
      this.recorder.onstop = () => {
        this.ended = true;
        this.endedAt = this.now();
        if (!this.failed) {
          this.phase = 'saving';
          this.message = 'Saving final video data. Keep this tab and the terminal open…';
          void this.drain();
        }
        this.changed();
      };
      this.recorder.onstart = () => {
        this.startedAt = this.now();
        this.phase = 'recording';
        this.message = 'Recording video. Put the stick in view, then start the sync countdown.';
        this.changed();
      };
      this.recorder.start(1000);
    } catch (error) {
      // An uncertain response can still have allocated a file: abandon by the same ID.
      if (this.id) {
        try { await this.send('/api/video/abandon', {id: this.id}); }
        catch { if (allocated) error.message += ' Restart the sensor session if the video remains active.'; }
      }
      this.phase = 'idle';
      this.message = error.message;
      this.changed();
      throw error;
    }
  }

  enqueue(blob) {
    if (!blob.size) return;
    for (let offset = 0; offset < blob.size; offset += CHUNK_BYTES) {
      const part = blob.slice(offset, offset + CHUNK_BYTES);
      this.queue.push({sequence: this.sequence++, blob: part});
      this.pendingBytes += part.size;
      this.bytes += part.size;
    }
    if (this.pendingBytes > MAX_PENDING_BYTES) this.stop('Video uploads fell behind; recording stopped to limit memory use.');
    if (!this.failed) void this.drain();
  }

  stop(warning = '') {
    if (warning) this.warning = warning;
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.phase = this.failed ? 'error' : 'saving';
      if (!this.failed) this.message = 'Stopping video and saving the final data…';
      this.recorder.stop();
      this.changed();
    }
  }

  retry() {
    if (this.phase !== 'error' || this.uploading || !this.ended) return;
    this.failed = false;
    this.phase = 'saving';
    this.message = 'Retrying video save…';
    this.changed();
    void this.drain();
  }

  async drain() {
    if (this.uploading || this.failed) return;
    this.uploading = true;
    try {
      while (this.queue.length) {
        const item = this.queue[0];
        await this.send(`/api/video/chunk/${this.id}/${item.sequence}`, item.blob, true);
        this.queue.shift();
        this.pendingBytes -= item.blob.size;
        this.changed();
      }
      if (this.ended) {
        this.file = await this.send('/api/video/finish', {
          id: this.id, chunks: this.sequence, bytes: this.bytes,
          duration_s: this.elapsed, warning: this.warning,
        });
        this.phase = 'saved';
        this.message = `Saved ${this.file.filename} in the sensor-session folder.${this.warning ? ` Warning: ${this.warning}` : ''}`;
      }
    } catch (error) {
      this.failed = true;
      this.phase = 'error';
      this.message = `Video is NOT fully saved: ${error.message} Keep this tab and the terminal open, then retry saving. Received data is kept as a .part file.`;
      this.stop();
    } finally {
      this.uploading = false;
      this.changed();
    }
  }
}
