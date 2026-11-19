// PersistentLicenseStore: JS-level persistence for EME licenses.
//
// Desktop Chrome's ClearKey/Widevine CDMs do not support the
// 'persistent-license' session type (verified: requestMediaKeySystemAccess
// rejects persistentState:'required' on this platform). This store mirrors
// the CDM-side persistent-license semantics per the EME spec: the issued
// license and its session id are kept in origin storage, and "loading" a
// persisted session reinstates the keys from storage WITHOUT a new license
// request to the server.
const PersistentLicenseStore = (() => {
  const STORAGE_KEY = 'eme-demo.persistent-license.v1';

  function save(record) {
    // record: { sessionId, keyIds, license, policy, savedAt }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record));
  }

  function load() {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function clear() {
    localStorage.removeItem(STORAGE_KEY);
  }

  return { save, load, clear };
})();
