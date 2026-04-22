/**
 * Helper Service for Social Media Autopilot
 * Runs alongside n8n on the VM. Handles:
 *   POST /upload-image  — saves base64 PNG to disk, returns public URL
 *   POST /proxy/devto   — forwards article JSON to Dev.to API (bypasses n8n body mangling)
 *   GET  /health        — health check
 *
 * Zero dependencies — uses only Node.js built-in modules.
 */
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFile } = require('child_process');
const { promisify } = require('util');
const execFileAsync = promisify(execFile);

const PORT = 3001;
const IMG_DIR = '/var/www/images';
const PUBLIC_URL = process.env.PUBLIC_URL || 'http://localhost';
const FREEIMAGE_KEY = '6d207e02198a847aa98d0a2a901485a5';  // public test key from freeimage.host docs

// Ensure image directory exists
if (!fs.existsSync(IMG_DIR)) {
  fs.mkdirSync(IMG_DIR, { recursive: true });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks).toString()));
    req.on('error', reject);
  });
}

function httpsRequest(options, body) {
  return new Promise((resolve, reject) => {
    const req = https.request(options, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json');

  try {
    // ─── Health check ───
    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200);
      return res.end(JSON.stringify({ status: 'ok', uptime: process.uptime() }));
    }

    // ─── Upload Image: PNG → JPEG → upload to freeimage.host (Meta trusts iili.io) ───
    if (req.method === 'POST' && req.url === '/upload-image') {
      const raw = await readBody(req);
      const { base64 } = JSON.parse(raw);
      if (!base64) {
        res.writeHead(400);
        return res.end(JSON.stringify({ error: 'Missing base64 field' }));
      }
      const id = crypto.randomBytes(12).toString('hex');
      const pngPath = path.join(IMG_DIR, `${id}.png.tmp`);
      const jpgFilename = `${id}.jpg`;
      const jpgPath = path.join(IMG_DIR, jpgFilename);
      fs.writeFileSync(pngPath, Buffer.from(base64, 'base64'));

      // Convert PNG → JPEG (Instagram requires JPEG)
      let jpgBase64 = base64;
      try {
        await execFileAsync('convert', [pngPath, '-strip', '-quality', '85', '-background', 'white', '-flatten', jpgPath]);
        fs.unlinkSync(pngPath);
        jpgBase64 = fs.readFileSync(jpgPath).toString('base64');
      } catch (e) {
        console.error(`[convert-fail] ${e.message.substring(0, 100)}`);
        fs.renameSync(pngPath, jpgPath);  // use as-is
      }
      const kb = Math.round(fs.statSync(jpgPath).size / 1024);
      const localUrl = `${PUBLIC_URL}/images/${jpgFilename}`;

      // Upload to freeimage.host (trusted by Meta/Instagram)
      let publicUrl = localUrl;  // fallback
      try {
        const form = `key=${encodeURIComponent(FREEIMAGE_KEY)}&type=base64&format=json&source=${encodeURIComponent(jpgBase64)}`;
        const resp = await httpsRequest({
          hostname: 'freeimage.host',
          port: 443,
          path: '/api/1/upload',
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': Buffer.byteLength(form),
          },
        }, form);
        const parsed = JSON.parse(resp.body);
        const url = parsed?.image?.url;
        if (url) {
          publicUrl = url;
          console.log(`[upload] ${jpgFilename} (${kb}KB JPEG) → ${url} [freeimage]`);
        } else {
          console.log(`[upload-fallback] ${jpgFilename} (${kb}KB) → ${localUrl} (freeimage failed: ${resp.body.substring(0, 80)})`);
        }
      } catch (e) {
        console.log(`[upload-fallback] ${jpgFilename} (${kb}KB) → ${localUrl} (freeimage error: ${e.message.substring(0, 80)})`);
      }

      res.writeHead(200);
      return res.end(JSON.stringify({ url: publicUrl, filename: jpgFilename, localUrl }));
    }

    // ─── Proxy to Dev.to ───
    if (req.method === 'POST' && req.url === '/proxy/devto') {
      const raw = await readBody(req);
      const { apiKey, articleBody } = JSON.parse(raw);
      // articleBody is the FULL JSON string: {"article":{...}}
      const data = typeof articleBody === 'string' ? articleBody : JSON.stringify(articleBody);
      const resp = await httpsRequest({
        hostname: 'dev.to',
        port: 443,
        path: '/api/articles',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'api-key': apiKey,
          'Accept': 'application/vnd.forem.api-v1+json',
          'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
          'Content-Length': Buffer.byteLength(data),
        },
      }, data);
      console.log(`[devto] ${resp.status} — ${resp.body.substring(0, 120)}`);
      res.writeHead(resp.status);
      return res.end(resp.body);
    }

    res.writeHead(404);
    res.end(JSON.stringify({ error: 'Not found' }));
  } catch (e) {
    console.error(`[error] ${e.message}`);
    res.writeHead(500);
    res.end(JSON.stringify({ error: e.message }));
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`Helper service running on http://127.0.0.1:${PORT}`);
  console.log(`  POST /upload-image  — save base64 image, return public URL`);
  console.log(`  POST /proxy/devto   — forward article to Dev.to API`);
  console.log(`  Public URL base: ${PUBLIC_URL}`);
});
