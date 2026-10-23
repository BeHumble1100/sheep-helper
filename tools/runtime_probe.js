(() => ({
  globalThisType: typeof globalThis,
  windowType: typeof window,
  documentType: typeof document,
  ccType: typeof globalThis.cc,
  windowCCType: typeof window !== "undefined" ? typeof window.cc : "n/a",
  requireType: typeof require,
  moduleType: typeof module,
  location: String(location.href),
  frames: Array.from({ length: window.frames.length }, (_, index) => {
    const frame = window.frames[index];
    return {
      index,
      location: (() => { try { return String(frame.location.href); } catch { return "cross-origin"; } })(),
      ccType: (() => { try { return typeof frame.cc; } catch { return "cross-origin"; } })(),
    };
  }),
  globalKeys: Object.keys(globalThis).slice(0, 200),
}))()
