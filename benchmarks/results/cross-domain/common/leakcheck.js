// Shared leak classification for the JavaScript runners.
// whole:    the value appears verbatim in the output.
// fragment: at least `window` consecutive characters of the value appear in the output
//           (the split-boundary symptom: the filter fired on one piece, the other leaked).
// masked(): the value is replaced by '#' so transcripts never carry the specimen.
'use strict';

// whole: value forwarded verbatim. fragment: a prefix or suffix of 4+ characters survived
// (with two-part splits the pieces are always a prefix and a suffix). none: neither.
function classify(output, value) {
  if (output.includes(value)) return 'whole';
  for (let k = 4; k < value.length; k++) {
    if (output.includes(value.slice(0, k)) || output.includes(value.slice(value.length - k))) return 'fragment';
  }
  return 'none';
}

function masked(output, value) {
  let out = output.split(value).join('#'.repeat(value.length));
  // mask any surviving fragment of 12+ chars as well
  for (let i = 0; i + 12 <= value.length; i++) {
    const piece = value.slice(i, i + 12);
    if (out.includes(piece)) out = out.split(piece).join('#'.repeat(12));
  }
  return out;
}

module.exports = { classify, masked };
