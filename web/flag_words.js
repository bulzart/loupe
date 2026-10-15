// Plain words for the flag record: what each kind, status, suspected-cause tag and pipeline stage means, so the Flags page reads without knowing the pipeline's own slugs.
// Nothing here replaces data: the raw value stays available (as the tooltip, and in the raw record at the bottom of every flag).

export const STATUS = {
  open: ['Open', 'Not solved yet: no changed request has been accepted.'],
  cleared: ['Cleared', 'Solved: a changed request was accepted. “What fixed it” says what was changed.'],
  persistent: ['Keeps happening', 'Changes were tried and this flag keeps coming back.'],
  archived: ['Archived', 'Kept for the record and left out of the default view.'],
};

export const KIND = {
  'moderation-block': ['Blocked by moderation', 'Higgsfield refused the request as a content or safety block.'],
  'refused-no-reason': ['Failed, no reason given', 'The job failed or was refused and Higgsfield did not say why.'],
  'media-flagged': ['An input file was flagged', 'One of the images, videos or elements that were sent was flagged.'],
  'output-flagged': ['The result was flagged', 'The video was made, then flagged or withheld.'],
  'preset-notice': ['A preset warned instead of running', 'A Higgsfield preset answered with a notice instead of starting a job.'],
  other: ['Something else', 'A problem that fits no other kind.'],
};

export const TAG = {
  'real-person-face': ['A real person’s face', 'A face that looks like a real, identifiable person.'],
  'celebrity-name': ['A celebrity’s name', 'The name of a famous person appears in the prompt or a caption.'],
  celebrity: ['A celebrity', 'A famous person.'],
  'brand-name': ['A brand name', 'A brand or product name that is not the client’s.'],
  'protected-ip': ['A protected character or IP', 'A character or other protected intellectual property.'],
  'nsfw-image': ['An image judged NSFW', 'An input image that was judged not safe for work.'],
  element: ['A saved element', 'A character or product element saved in Higgsfield.'],
  'prompt-wording': ['Words in the prompt', 'Particular words or phrases in the prompt.'],
  'preset-notice': ['A preset notice', 'The preset itself answered with a notice.'],
  'output-withheld': ['Output withheld', 'The video was made but not delivered.'],
  'unknown-trigger': ['Cause not known yet', 'Nothing found so far explains it.'],
  'video-reference': ['The reference video', 'The video that was sent as the reference for motion or framing.'],
  'image-reference': ['A reference image', 'One of the reference images that were sent.'],
  preflight: ['Caught before generating', 'Stopped by the preflight check, before any credits were spent.'],
  seedance: ['The Seedance model', 'Specific to the Seedance model.'],
  dark: ['A dark scene', 'A dark or low-light scene.'],
};

export const STAGE = {
  preflight: ['before generating', 'Caught by the preflight check, before any credits were spent.'],
  submit: ['when submitting', 'The request was refused as it was submitted.'],
  job: ['while the job ran', 'The job started and then failed or was refused.'],
  output: ['on the finished result', 'The video was made, then flagged or withheld.'],
};

const pretty = (s) => { const t = String(s || '').replace(/[-_]+/g, ' ').trim(); return t ? t[0].toUpperCase() + t.slice(1) : ''; };
const info = (map, k, fallback) => { const [label, hint] = map[k] || [pretty(k) || fallback, '']; return { label, hint }; };
export const statusInfo = (s) => info(STATUS, s, 'Unknown');
export const kindInfo = (k) => info(KIND, k, 'Something else');
export const tagInfo = (t) => info(TAG, t, '');
export const stageInfo = (s) => info(STAGE, s, '');

// "3 h ago": the exact time stays in the tooltip.
export function ago(iso) {
  const t = Date.parse(iso);
  if (!t) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  const d = Math.round(s / 86400);
  return d + (d === 1 ? ' day ago' : ' days ago');
}

export const clip = (s, n) => { const t = String(s || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t; };

// "Video 10 · attempt a4 · wan3_0_prime · CELSIUS Cherry Cola can"
export function whereLine(e) {
  const w = e.where || {};
  return [w.video ? `Video ${w.video}${w.attempt ? ' · attempt ' + w.attempt : ''}${w.clip ? ' · clip ' + w.clip : ''}` : '', w.model, w.product].filter(Boolean).join(' · ');
}

// One plain sentence that says what this flag is.
export function sentence(e) {
  const w = e.where || {};
  const st = w.stage ? stageInfo(w.stage).label : '';
  const parts = [kindInfo(e.kind).label + (st ? ', ' + st : '') + '.'];
  const where = whereLine(e);
  if (where) parts.push(where + '.');
  if (e.credits) parts.push('Credits: ' + e.credits + '.');
  return parts.join(' ');
}
