function _wrapText(ctx, text, maxWidth, maxLines) {
        const words = (text || '').split(/\s+/);
        const lines = [];
        let current = '';
        for (const word of words) {
            const trial = current ? current + ' ' + word : word;
            if (ctx.measureText(trial).width > maxWidth && current) {
                lines.push(current);
                current = word;
                if (lines.length >= maxLines) break;
            } else {
                current = trial;
            }
        }
        if (current && lines.length < maxLines) lines.push(current);
        // If we still have overflow, ellipsize the last line.
        if (lines.length === maxLines) {
            let last = lines[lines.length - 1];
            while (ctx.measureText(last + '…').width > maxWidth && last.length > 0) {
                last = last.slice(0, -1);
            }
            lines[lines.length - 1] = last + '…';
        }
        return lines;}
const calls = [];
const ctx = { measureText: (s) => ({ width: s.length * 12 }) };
const lines = _wrapText(ctx, 'word '.repeat(40).trim(), 200, 3);
console.log('LINES', lines.length, JSON.stringify(lines));
