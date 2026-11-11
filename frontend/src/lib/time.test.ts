import {
  addDays,
  clockFormatter,
  dayFormatter,
  formatCivilDay,
  localDayStarts,
  safeZone,
  weekOf,
} from "./time";

const at = (iso: string): number => Date.parse(iso) / 1000;

describe("dayFormatter", () => {
  it("gives the local civil date in the zone, not the UTC one", () => {
    expect(dayFormatter("Asia/Tokyo")(at("2026-11-12T23:30:00Z"))).toBe("2026-11-13");
    expect(dayFormatter("UTC")(at("2026-11-12T23:30:00Z"))).toBe("2026-11-12");
    expect(dayFormatter("America/Los_Angeles")(at("2026-11-13T05:00:00Z"))).toBe("2026-11-12");
  });
});

describe("clockFormatter", () => {
  it("gives the local wall-clock time", () => {
    expect(clockFormatter("Asia/Kolkata")(at("2026-11-12T22:45:00Z"))).toBe("04:15");
  });
});

describe("weekOf", () => {
  it("runs Monday to Sunday and moves by whole weeks", () => {
    expect(weekOf("2026-11-11")).toEqual([
      "2026-11-09",
      "2026-11-10",
      "2026-11-11",
      "2026-11-12",
      "2026-11-13",
      "2026-11-14",
      "2026-11-15",
    ]);
    expect(weekOf("2026-11-09")[0]).toBe("2026-11-09");
    expect(weekOf("2026-11-15")[0]).toBe("2026-11-09");
    expect(weekOf("2026-11-11", 1)[0]).toBe("2026-11-16");
    expect(weekOf("2026-11-11", -1)[6]).toBe("2026-11-08");
  });

  it("crosses month and year ends", () => {
    expect(weekOf("2026-12-31")).toEqual([
      "2026-12-28",
      "2026-12-29",
      "2026-12-30",
      "2026-12-31",
      "2027-01-01",
      "2027-01-02",
      "2027-01-03",
    ]);
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
  });
});

describe("localDayStarts", () => {
  it("finds the local midnights of a zone with a half-hour offset", () => {
    const starts = localDayStarts(
      at("2026-11-11T00:00:00Z"),
      at("2026-11-13T00:00:00Z"),
      "Asia/Kolkata",
    );
    expect(starts).toEqual([
      { day: "2026-11-12", at: at("2026-11-11T18:30:00Z") },
      { day: "2026-11-13", at: at("2026-11-12T18:30:00Z") },
    ]);
  });

  it("keeps the 23-hour day of a daylight saving change", () => {
    const starts = localDayStarts(
      at("2026-03-28T12:00:00Z"),
      at("2026-03-30T12:00:00Z"),
      "Europe/Paris",
    );
    expect(starts).toEqual([
      { day: "2026-03-29", at: at("2026-03-28T23:00:00Z") },
      { day: "2026-03-30", at: at("2026-03-29T22:00:00Z") },
    ]);
  });
});

describe("safeZone and formatCivilDay", () => {
  it("falls back to UTC for an unknown zone and writes civil dates in French", () => {
    expect(safeZone("Mars/Olympus")).toBe("UTC");
    expect(safeZone("Asia/Tokyo")).toBe("Asia/Tokyo");
    expect(formatCivilDay("2026-11-09", { weekday: "long", day: "numeric", month: "long" })).toBe(
      "lundi 9 novembre",
    );
  });
});
