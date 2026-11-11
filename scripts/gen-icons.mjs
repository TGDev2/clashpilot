// Draws the PWA icons: an abstract compass ring and arrow in the colours of the app theme.
// Original artwork made of plain geometry: nothing of the game is used. Zero dependency.
//   node scripts/gen-icons.mjs        write frontend/public/icons/*.png
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { crc32, deflateSync } from 'node:zlib';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const ICON_DIR = join(ROOT, 'frontend', 'public', 'icons');

export const ICONS = [
  { file: 'icon-192.png', size: 192 },
  { file: 'icon-512.png', size: 512 },
  { file: 'icon-maskable-512.png', size: 512 },
  { file: 'apple-touch-icon.png', size: 180 },
];

const INK = [0x0a, 0x11, 0x14];
const DECK = [0x17, 0x24, 0x29];
const SIGNAL = [0x6f, 0xb7, 0xd6];
const BONE = [0xeb, 0xe6, 0xd6];
const AMBER = [0xe6, 0xb3, 0x4c];

// Everything stays inside a disc of radius 0.4 (the safe zone of maskable icons).
const ARROW = [
  [0.66, 0.34],
  [0.39, 0.47],
  [0.48, 0.52],
  [0.53, 0.61],
];

function inPolygon(x, y, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** The colour of the point (x, y) of the unit square. */
function colourAt(x, y) {
  if (inPolygon(x, y, ARROW)) return BONE;
  const distance = Math.hypot(x - 0.5, y - 0.5);
  if (Math.hypot(x - 0.5, y - 0.2) < 0.03) return AMBER;
  if (distance >= 0.29 && distance <= 0.34) return SIGNAL;
  if (distance < 0.34) return DECK;
  return INK;
}

/** RGBA pixels of a `size` x `size` icon, 3 x 3 samples per pixel for smooth edges. */
export function renderIcon(size) {
  const samples = 3;
  const pixels = Buffer.alloc(size * size * 4);
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      const sum = [0, 0, 0];
      for (let sy = 0; sy < samples; sy++) {
        for (let sx = 0; sx < samples; sx++) {
          const colour = colourAt((px + (sx + 0.5) / samples) / size, (py + (sy + 0.5) / samples) / size);
          for (let c = 0; c < 3; c++) sum[c] += colour[c];
        }
      }
      const offset = (py * size + px) * 4;
      for (let c = 0; c < 3; c++) pixels[offset + c] = Math.round(sum[c] / (samples * samples));
      pixels[offset + 3] = 255;
    }
  }
  return pixels;
}

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, 'latin1');
  const tail = Buffer.alloc(4);
  tail.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, tail]);
}

/** A PNG file (8-bit RGBA) of the icon. */
export function encodePng(size) {
  const pixels = renderIcon(size);
  const rows = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    pixels.copy(rows, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(rows, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  mkdirSync(ICON_DIR, { recursive: true });
  for (const { file, size } of ICONS) {
    writeFileSync(join(ICON_DIR, file), encodePng(size));
    console.log(`wrote ${join(ICON_DIR, file)}`);
  }
}
