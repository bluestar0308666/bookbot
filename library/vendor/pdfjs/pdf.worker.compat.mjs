if (typeof URL.parse !== "function") {
  URL.parse = function (url, base) {
    try {
      return new URL(url, base);
    } catch {
      return null;
    }
  };
}
if (typeof Promise.try !== "function") {
  Promise.try = function (fn, ...args) {
    return new Promise((resolve) => resolve(fn(...args)));
  };
}
await import("./pdf.worker.min.mjs");
