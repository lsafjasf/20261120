// DrmPlayer: EME persistent-license demo player (ClearKey).
//
// NOTE on fidelity: this Linux desktop Chrome build rejects the
// 'persistent-license' session type for both ClearKey and Widevine
// (verified via requestMediaKeySystemAccess). The demo therefore runs real
// EME ClearKey sessions for all CDM interaction, while license persistence
// is emulated by PersistentLicenseStore (localStorage) exactly the way a
// CDM persists licenses: restore = reinstate keys from storage, with NO new
// license request. Key statuses 'expired' / 'output-restricted' cannot be
// emitted by the ClearKey CDM, so the mock license server attaches a
// `policy.keyStatus` override which the player applies through the same
// code path as real keystatuseschange events.
const DEMO_KEY_ID = 'oR5w1fZgQd-5z5z5z5z5z5w'; // must match server.js

class DrmPlayer {
  constructor(video, ui) {
    this.video = video;
    this.ui = ui; // { setStatus(text), showPrompt(text, kind), log(line) }
    this.report = {
      scenario: null,
      sessionId: null,
      licenseSource: null,
      loadTimeMs: null,
      keyStatus: null,
      playing: false,
      prompt: null,
      licenseError: null,
    };
  }

  async init() {
    const config = [{
      initDataTypes: ['keyids'],
      videoCapabilities: [{ contentType: 'video/mp4; codecs="avc1.42E01E"' }],
    }];
    const access = await navigator.requestMediaKeySystemAccess('org.w3.clearkey', config);
    this.mediaKeys = await access.createMediaKeys();
    await this.video.setMediaKeys(this.mediaKeys);
  }

  // One entry point for both first acquisition and restore: the session
  // 'message' handler serves the license from the persistent store when
  // present, and only then falls back to the network license server.
  async openSession(scenario) {
    this.report.scenario = scenario;
    const stored = PersistentLicenseStore.load();
    const t0 = performance.now();

    const session = this.mediaKeys.createSession('temporary');
    this.session = session;

    const done = new Promise((resolve, reject) => {
      this._resolveKeys = resolve;
      this._rejectKeys = reject;
      setTimeout(() => reject(new Error('keystatuseschange 超时')), 5000);
    });

    session.addEventListener('keystatuseschange', () => {
      const statuses = {};
      session.keyStatuses.forEach((s, kid) => { statuses[kid] = s; });
      this._onKeyStatuses(statuses, stored);
      if (this._resolveKeys) this._resolveKeys();
    });

    session.addEventListener('message', async (ev) => {
      try {
        const fromStore = PersistentLicenseStore.load();
        if (fromStore) {
          this.report.licenseSource = 'persistent-store (localStorage, 无网络请求)';
          this.report.sessionId = fromStore.sessionId;
          this._policy = fromStore.policy;
          await session.update(new TextEncoder().encode(JSON.stringify(fromStore.license)));
          return;
        }
        this.report.licenseSource = 'network (license server)';
        const res = await fetch(`/api/license?scenario=${encodeURIComponent(scenario)}`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            keyIds: [DEMO_KEY_ID],
            initData: btoa(String.fromCharCode(...new Uint8Array(ev.message))),
          }),
        });
        const payload = await res.json().catch(() => null);
        if (!res.ok) {
          const msg = payload && payload.error ? payload.error.message : `HTTP ${res.status}`;
          throw new Error(`许可加载失败: ${msg}`);
        }
        this._policy = payload.policy || { keyStatus: 'usable' };
        const sessionId = 'sess-' + Date.now().toString(36);
        PersistentLicenseStore.save({
          sessionId,
          keyIds: [DEMO_KEY_ID],
          license: payload.license,
          policy: this._policy,
          savedAt: new Date().toISOString(),
        });
        this.report.sessionId = sessionId;
        await session.update(new TextEncoder().encode(JSON.stringify(payload.license)));
      } catch (err) {
        if (this._rejectKeys) this._rejectKeys(err);
      }
    });

    const initData = new TextEncoder().encode(JSON.stringify({ kids: [DEMO_KEY_ID] }));
    await session.generateRequest('keyids', initData);
    await done;
    this.report.loadTimeMs = Math.round((performance.now() - t0) * 100) / 100;
    return this.report;
  }

  _onKeyStatuses(cdmStatuses, stored) {
    // Effective status: policy override (simulated) wins over CDM status.
    const policy = this._policy || (stored && stored.policy) || {};
    const cdmStatus = Object.values(cdmStatuses)[0] || 'unknown';
    const effective = policy.keyStatus || cdmStatus;
    this.report.keyStatus = effective;
    this.report.cdmKeyStatus = cdmStatus;

    if (effective === 'usable') {
      this.report.playing = true;
      this.report.prompt = null;
      this.ui.setStatus('▶ 播放中（密钥可用）');
      this.ui.showPrompt('', 'ok');
    } else if (effective === 'expired') {
      this.report.playing = false;
      this.report.prompt = `许可已过期，请重新获取许可。${policy.message || ''}`;
      this.ui.setStatus('■ 已停止（密钥过期）');
      this.ui.showPrompt(this.report.prompt, 'error');
    } else if (effective === 'output-restricted') {
      this.report.playing = false;
      this.report.prompt = `输出受限：当前显示链路不满足内容保护要求，无法播放。${policy.message || ''}`;
      this.ui.setStatus('■ 已停止（输出受限）');
      this.ui.showPrompt(this.report.prompt, 'error');
    } else {
      this.report.playing = false;
      this.report.prompt = `密钥状态异常: ${effective}`;
      this.ui.setStatus(`■ 已停止 (${effective})`);
      this.ui.showPrompt(this.report.prompt, 'error');
    }
  }
}
