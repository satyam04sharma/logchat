/* SPDX-License-Identifier: AGPL-3.0-only */
import { createAvatar } from '@bible-strong/avatar-web';
import definition from './logchat.avatar.json';

const mount = document.getElementById('reactiveMascot');
const status = document.getElementById('mascotStatus');
const toggle = document.getElementById('motionToggle');
const copy = document.querySelector('[data-copy="pull"]');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let avatar;
let paused = false;
let visible = true;
let reaction;
let copyPending = false;
let flowFinished = false;

function react(name, text) {
  status.textContent = text;
  if (!avatar || paused || !visible || document.hidden || reduced.matches) return;
  reaction = name;
  avatar.play(name);
}
try {
  avatar = createAvatar(mount, {
    definition, size: '100%', ariaLabel: 'Logchat mascot',
    onAnimationEnd() { reaction = undefined; },
  });
  mount.dataset.ready = 'true';
  react('waking', 'Ready when you are.');
} catch (error) {
  console.error('Mascot could not load; static artwork retained.', error);
}
const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
copy.addEventListener('pointerenter', () => {
  if (finePointer.matches && !copyPending) react('curious', 'Copy the install command to get started.');
});
copy.addEventListener('focus', () => {
  status.textContent = 'Copy the install command to get started.';
});
copy.addEventListener('click', async () => {
  if (copyPending) return;
  copyPending = true;
  try {
    await navigator.clipboard.writeText('python -m pip install .');
    copy.textContent = 'Copied';
    react('celebrate', 'Command copied. Run it from the cloned repository.');
  } catch {
    copy.textContent = 'Select command';
    const selection = getSelection();
    const range = document.createRange();
    range.selectNodeContents(document.getElementById('pullCmd'));
    selection.removeAllRanges();
    selection.addRange(range);
    react('confused', 'Clipboard access failed. The command is selected—copy it manually.');
  } finally {
    copyPending = false;
  }
});
function resumeAvatar() {
  if (avatar && !paused && visible && !document.hidden && !reduced.matches && reaction) avatar.play(reaction);
}
toggle.addEventListener('click', () => {
  if (flowFinished) {
    document.documentElement.classList.add('motion-reset');
    void document.documentElement.offsetWidth;
    document.documentElement.classList.remove('motion-reset');
    flowFinished = false;
    paused = false;
    react('waking', 'Logs become compact, searchable context.');
  } else {
    paused = !paused;
    if (paused) avatar?.pause(); else resumeAvatar();
  }
  document.documentElement.classList.toggle('motion-paused', paused);
  toggle.textContent = paused ? 'Resume animation' : 'Pause animation';
  toggle.setAttribute('aria-pressed', String(paused));
});
document.querySelector('.flow-stages li:last-child').addEventListener('animationend', () => {
  flowFinished = true;
  toggle.textContent = 'Replay animation';
  toggle.setAttribute('aria-pressed', 'false');
});
new IntersectionObserver(([entry]) => {
  visible = entry.isIntersecting;
  if (!visible) avatar?.pause(); else resumeAvatar();
}).observe(mount);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) avatar?.pause(); else resumeAvatar();
});
reduced.addEventListener('change', () => {
  if (reduced.matches) { avatar?.stop(); reaction = undefined; }
});
