// The product rename (kriflow#596): this browser's saved choices moved from `swarmify.*` keys to
// `kriflow.*`. Each old key is copied once, when the new one is absent, and left in place, so an
// older build after a rollback still finds it. Kept forever: the keys are a few bytes.
const OLD = "swarmify.";
const NEW = "kriflow.";

export function copyForward(storage: Storage): void {
  const old = Array.from({ length: storage.length }, (_, i) => storage.key(i)).filter((k): k is string => !!k?.startsWith(OLD));
  for (const key of old) {
    const renamed = NEW + key.slice(OLD.length);
    const value = storage.getItem(key);
    if (value !== null && storage.getItem(renamed) === null) storage.setItem(renamed, value);
  }
}

// Imported first by main.tsx, so it runs before any module reads a key.
try {
  copyForward(localStorage);
  copyForward(sessionStorage);
} catch {
  // storage refused (a private window): nothing was saved under the old names either
}
