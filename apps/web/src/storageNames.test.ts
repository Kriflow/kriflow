import { beforeEach, expect, test } from "vitest";

import { copyForward } from "./storageNames";

beforeEach(() => localStorage.clear());

test("each old key is copied once, a newer key is never overwritten, and the old ones stay", () => {
  localStorage.setItem("swarmify.skin", "pied-piper");
  localStorage.setItem("swarmify.setup-hidden.world_1", "1");
  localStorage.setItem("swarmify.textSize", "large");
  localStorage.setItem("kriflow.textSize", "larger");
  localStorage.setItem("other", "x");
  copyForward(localStorage);
  expect(localStorage.getItem("kriflow.skin")).toBe("pied-piper");
  expect(localStorage.getItem("kriflow.setup-hidden.world_1")).toBe("1");
  expect(localStorage.getItem("kriflow.textSize")).toBe("larger");
  expect(localStorage.getItem("swarmify.skin")).toBe("pied-piper");
  expect(localStorage.getItem("kriflow.other")).toBeNull();
  localStorage.setItem("kriflow.skin", "hooli");
  copyForward(localStorage);
  expect(localStorage.getItem("kriflow.skin")).toBe("hooli");
});
