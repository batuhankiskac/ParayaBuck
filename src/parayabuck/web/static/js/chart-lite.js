(function () {
    'use strict';

    class ChartLite {
        constructor(context, config) {
            this.context = context;
            this.canvas = context.canvas;
            this.data = config.data || { labels: [], datasets: [] };
            this.options = config.options || {};
            this.canvas.style.width = '100%';
            this.canvas.style.height = '100%';
            this.resizeObserver = typeof ResizeObserver === 'undefined'
                ? null
                : new ResizeObserver(() => this.update());
            if (this.resizeObserver && this.canvas.parentElement) {
                this.resizeObserver.observe(this.canvas.parentElement);
            }
            this.update();
        }

        update() {
            const canvas = this.canvas;
            const ratio = window.devicePixelRatio || 1;
            const parent = canvas.parentElement;
            const width = Math.max(320, parent?.clientWidth || canvas.clientWidth || 640);
            const height = Math.max(180, parent?.clientHeight || canvas.clientHeight || 260);
            const bitmapWidth = Math.round(width * ratio);
            const bitmapHeight = Math.round(height * ratio);
            if (canvas.width !== bitmapWidth || canvas.height !== bitmapHeight) {
                canvas.width = bitmapWidth;
                canvas.height = bitmapHeight;
            }
            const ctx = this.context;
            ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            ctx.clearRect(0, 0, width, height);
            const dataset = (this.data.datasets || [])[0];
            const values = dataset ? dataset.data.map(Number).filter(Number.isFinite) : [];
            const labels = this.data.labels || [];

            if (values.length === 0) {
                ctx.fillStyle = '#64748B';
                ctx.font = '12px Inter, system-ui, sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('Portföy verisi bekleniyor…', width / 2, height / 2);
                return;
            }

            const padding = { top: 16, right: 16, bottom: 30, left: 62 };
            const rawMin = Math.min(...values);
            const rawMax = Math.max(...values);
            const flatPadding = Math.max(1, Math.abs(rawMax) * 0.001);
            const min = rawMin === rawMax ? rawMin - flatPadding : rawMin;
            const max = rawMin === rawMax ? rawMax + flatPadding : rawMax;
            const range = max - min;
            const plotWidth = width - padding.left - padding.right;
            const plotHeight = height - padding.top - padding.bottom;
            const xAt = (index) => (
                values.length === 1
                    ? padding.left + plotWidth / 2
                    : padding.left + index * plotWidth / (values.length - 1)
            );
            const yAt = (value) => padding.top + (max - value) * plotHeight / range;

            ctx.font = '10px Inter, system-ui, sans-serif';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';
            for (let index = 0; index <= 4; index += 1) {
                const ratioFromTop = index / 4;
                const y = padding.top + ratioFromTop * plotHeight;
                const value = max - ratioFromTop * range;
                ctx.beginPath();
                ctx.moveTo(padding.left, y);
                ctx.lineTo(width - padding.right, y);
                ctx.strokeStyle = 'rgba(148, 163, 184, 0.12)';
                ctx.lineWidth = 1;
                ctx.stroke();
                ctx.fillStyle = '#64748B';
                ctx.fillText(
                    '$' + value.toLocaleString('en-US', { maximumFractionDigits: 0 }),
                    padding.left - 8,
                    y
                );
            }

            if (labels.length > 0) {
                ctx.fillStyle = '#64748B';
                ctx.textBaseline = 'bottom';
                ctx.textAlign = 'left';
                ctx.fillText(labels[0], padding.left, height - 2);
                if (labels.length > 1) {
                    ctx.textAlign = 'right';
                    ctx.fillText(labels[labels.length - 1], width - padding.right, height - 2);
                }
            }

            if (values.length > 1 && dataset.backgroundColor) {
                ctx.beginPath();
                values.forEach((value, index) => {
                    const x = xAt(index);
                    const y = yAt(value);
                    if (index === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                });
                ctx.lineTo(xAt(values.length - 1), padding.top + plotHeight);
                ctx.lineTo(xAt(0), padding.top + plotHeight);
                ctx.closePath();
                ctx.fillStyle = dataset.backgroundColor;
                ctx.fill();
            }

            ctx.beginPath();
            values.forEach((value, index) => {
                const x = xAt(index);
                const y = yAt(value);
                if (index === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.strokeStyle = dataset.borderColor || '#4ade80';
            ctx.lineWidth = dataset.borderWidth || 2;
            ctx.stroke();

            const lastIndex = values.length - 1;
            ctx.beginPath();
            ctx.arc(xAt(lastIndex), yAt(values[lastIndex]), 3.5, 0, Math.PI * 2);
            ctx.fillStyle = dataset.pointBackgroundColor || dataset.borderColor || '#4ade80';
            ctx.fill();
        }
    }

    window.Chart = ChartLite;
}());
