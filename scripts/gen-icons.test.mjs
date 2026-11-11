import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { inflateSync } from 'node:zlib';
import { encodePng, ICON_DIR, ICONS } from './gen-icons.mjs';

test('encodePng writes a PNG of the requested size', () => {
  const png = encodePng(64);
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.equal(png.readUInt32BE(16), 64);
  assert.equal(png.readUInt32BE(20), 64);
  const idat = png.indexOf('IDAT');
  const length = png.readUInt32BE(idat - 4);
  assert.equal(inflateSync(png.subarray(idat + 4, idat + 4 + length)).length, (64 * 4 + 1) * 64);
});

test('the committed icons have the size the script declares', () => {
  for (const { file, size } of ICONS) {
    const png = readFileSync(join(ICON_DIR, file));
    assert.equal(png.readUInt32BE(16), size, `${file}: run node scripts/gen-icons.mjs`);
    assert.equal(png.readUInt32BE(20), size, `${file}: run node scripts/gen-icons.mjs`);
  }
});
