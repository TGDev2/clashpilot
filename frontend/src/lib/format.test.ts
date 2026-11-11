import { formatDuration, formatSigned, resourceLabel } from "./format";

describe("formatDuration", () => {
  it("writes days, hours and minutes and drops the empty parts", () => {
    expect(formatDuration(9_000)).toBe("2 h 30 min");
    expect(formatDuration(7_200)).toBe("2 h");
    expect(formatDuration(90_000)).toBe("1 j 1 h");
    expect(formatDuration(0)).toBe("0 min");
  });
});

describe("formatSigned", () => {
  it("signs non-zero numbers and leaves zero plain", () => {
    expect(formatSigned(2.5)).toBe("+2,5");
    expect(formatSigned(0)).toBe("0");
    expect(formatSigned(-1.25)).toMatch(/^[-−]1,25$/);
  });
});

describe("resourceLabel", () => {
  it("names a known resource and keeps an unknown key as is", () => {
    expect(resourceLabel("gold")).toBe("Or");
    expect(resourceLabel("mystery")).toBe("mystery");
  });
});
