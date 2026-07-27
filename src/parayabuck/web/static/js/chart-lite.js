(function () {
    'use strict';

    class ChartLite {
        constructor(context, config) {
            this.context = context;
            this.canvas = context.canvas;
            this.data = config.data || { labels: [], datasets: [] };
            this.options = config.options || {};
            this.update();
        }

        update() {
            const canvas = this.canvas;
            const ratio = window.devicePixelRatio || 1;
            const width = Math.max(320, canvas.clientWidth || canvas.width || 640);
            const height = Math.max(180, canvas.clientHeight || canvas.height || 260);
            if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
                canvas.width = width * ratio;
                canvas.height = height * ratio;
            }
            const ctx = this.context;
            ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
            ctx.clearRect(0, 0, width, height);
            const dataset = (this.data.datasets || [])[0];
            const values = dataset ? dataset.data.map(Number).filter(Number.isFinite) : [];
            if (values.length < 2) return;
            const padding = 18;
            const min = Math.min(...values);
            const max = Math.max(...values);
            const range = Math.max(1, max - min);
            ctx.beginPath();
            values.forEach((value, index) => {
                const x = padding + index * (width - padding * 2) / (values.length - 1);
                const y = height - padding - (value - min) * (height - padding * 2) / range;
                if (index === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            });
            ctx.strokeStyle = dataset.borderColor || '#4ade80';
            ctx.lineWidth = dataset.borderWidth || 2;
            ctx.stroke();
        }
    }

    window.Chart = ChartLite;
}());
