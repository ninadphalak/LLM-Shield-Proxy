// Generates the test values used by every cross-domain run and writes them to the path given
// as argv[2]. Keys are freshly generated throwaway RSA keys; nothing here is a real secret.
// The fixtures file is never committed (see .gitignore in this directory).
'use strict';
const fs = require('fs');
const crypto = require('crypto');

function pem(bits) {
  const { privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: bits });
  return privateKey.export({ type: 'pkcs1', format: 'pem' }).trim();
}

const fixtures = {
  email: 'jane.doe@example.com',
  ssn: '078-05-1120',
  pem2048: pem(2048),
  pem4096: pem(4096),
};
fs.writeFileSync(process.argv[2], JSON.stringify(fixtures, null, 2));
for (const [k, v] of Object.entries(fixtures)) console.log(k, v.length, 'chars');
