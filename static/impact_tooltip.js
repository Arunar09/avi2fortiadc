/**
 * Impact Tooltip System — Migration Operator Console
 * Native JS implementation for interactive popovers.
 */

class ImpactTooltipManager {
    constructor() {
        this.popover = null;
        this.currentCell = null;
        this.isSticky = false;
        this.init();
    }

    init() {
        // Create the popover element if it doesn't exist
        if (!document.getElementById('impact-popover')) {
            this.popover = document.createElement('div');
            this.popover.id = 'impact-popover';
            this.popover.className = 'impact-popover';
            document.body.appendChild(this.popover);
        } else {
            this.popover = document.getElementById('impact-popover');
        }

        // Global listeners
        document.addEventListener('mouseover', (e) => this.handleMouseOver(e));
        document.addEventListener('mouseout', (e) => this.handleMouseOut(e));
        document.addEventListener('click', (e) => this.handleClick(e));
        
        // Window resize - close popover to prevent misplacement
        window.addEventListener('resize', () => this.hide());
    }

    handleMouseOver(e) {
        if (this.isSticky) return; // Don't change content if sticky
        const cell = e.target.closest('.impact-cell');
        if (!cell) return;

        this.show(cell);
    }

    handleMouseOut(e) {
        if (this.isSticky) return;
        const cell = e.target.closest('.impact-cell');
        if (!cell) return;
        
        // Small delay to allow moving mouse TO the popover if we wanted, 
        // but for now simple hide
        this.hide();
    }

    handleClick(e) {
        const cell = e.target.closest('.impact-cell');
        const closeBtn = e.target.closest('.popover-close');
        const copyBtn = e.target.closest('.popover-copy');

        if (copyBtn) {
            this.handleCopy();
            return;
        }

        if (closeBtn) {
            this.isSticky = false;
            this.hide();
            return;
        }

        if (cell) {
            // Toggle sticky for the same cell, or switch to new cell
            if (this.currentCell === cell && this.isSticky) {
                this.isSticky = false;
                this.hide();
            } else {
                this.isSticky = true;
                this.show(cell);
                this.popover.classList.add('sticky');
            }
            return;
        }

        // Click outside closes sticky
        if (this.isSticky && !this.popover.contains(e.target)) {
            this.isSticky = false;
            this.hide();
        }
    }

    show(cell) {
        this.currentCell = cell;
        const data = this.parseCellData(cell);
        if (!data) {
            this.hide();
            return;
        }

        this.render(data);
        this.position(cell);
        
        this.popover.classList.add('active');
        if (!this.isSticky) {
            this.popover.classList.remove('sticky');
        }
    }

    hide() {
        if (this.isSticky) return;
        this.popover.classList.remove('active');
        this.popover.classList.remove('sticky');
        this.currentCell = null;
    }

    parseCellData(cell) {
        const raw = cell.getAttribute('data-impact-details');
        if (!raw) return null;
        try {
            return JSON.parse(raw);
        } catch (e) {
            console.error("Failed to parse impact data", e);
            return null;
        }
    }

    position(cell) {
        const rect = cell.getBoundingClientRect();
        const pRect = this.popover.getBoundingClientRect();
        
        let top = rect.bottom + window.scrollY + 10;
        let left = rect.left + window.scrollX;

        // Viewport collision detection
        if (left + pRect.width > window.innerWidth) {
            left = window.innerWidth - pRect.width - 20;
        }
        if (top + pRect.height > window.innerHeight + window.scrollY) {
            top = rect.top + window.scrollY - pRect.height - 10;
        }

        this.popover.style.top = `${top}px`;
        this.popover.style.left = `${left}px`;
    }

    render(data) {
        const title = `${data.tenant} — ${data.category.toUpperCase()}`;
        const sharedSet = new Set(data.shared_inc || []);
        
        let html = `
            <div class="popover-header">
                <div class="popover-title">${title}</div>
                <div class="popover-actions">
                    <button class="btn btn-sm btn-ghost popover-copy" style="padding: 2px 6px; font-size: 0.7rem;">📋 Copy</button>
                    ${this.isSticky ? '<button class="popover-close">✕</button>' : ''}
                </div>
            </div>
            <div class="popover-body">
        `;

        if (data.inc && (Array.isArray(data.inc) ? data.inc.length : Object.keys(data.inc).length) > 0) {
            html += this.renderSection('Included', data.inc, '✅', sharedSet);
        }
        if (data.ctx && (Array.isArray(data.ctx) ? data.ctx.length : Object.keys(data.ctx).length) > 0) {
            html += this.renderSection('Context Only', data.ctx, 'ℹ️', sharedSet);
        }
        if (data.exc && (Array.isArray(data.exc) ? data.exc.length : Object.keys(data.exc).length) > 0) {
            html += this.renderSection('Excluded', data.exc, '❌', sharedSet);
        }

        if (!data.inc && !data.ctx && !data.exc) {
            html += '<div class="text-mini text-muted">No objects identified in this category.</div>';
        }

        html += '</div>';
        this.popover.innerHTML = html;
    }

    renderSection(label, items, icon, sharedSet) {
        let contentHtml = '';
        
        if (Array.isArray(items)) {
            // Simple flat list
            contentHtml = `<ul class="popover-list">${items.map(name => this.renderItem(name, sharedSet)).join('')}</ul>`;
        } else {
            // Categorized dictionary
            contentHtml = Object.entries(items).map(([cat, names]) => `
                <div style="margin-left: 8px; margin-top: 6px;">
                    <div style="font-size: 0.65rem; color: #5a6070; font-weight: 700; border-bottom: 1px solid rgba(255,255,255,0.03); margin-bottom: 4px;">
                        ${cat.toUpperCase()} (${names.length})
                    </div>
                    <ul class="popover-list">${names.map(name => this.renderItem(name, sharedSet)).join('')}</ul>
                </div>
            `).join('');
        }

        return `
            <div class="popover-section">
                <div class="popover-section-title">${icon} ${label}</div>
                ${contentHtml}
            </div>
        `;
    }

    renderItem(name, sharedSet) {
        const isShared = sharedSet.has(name);
        if (isShared) {
            return `<li>${name} <span class="badge badge-blue" style="font-size: 0.6rem; padding: 0 4px; border:none; opacity: 0.8;">Shared</span></li>`;
        }
        return `<li>${name}</li>`;
    }

    handleCopy() {
        const lists = this.popover.querySelectorAll('.popover-list');
        const allNames = [];
        lists.forEach(list => {
            list.querySelectorAll('li').forEach(li => {
                allNames.push(li.innerText);
            });
        });

        const text = allNames.join('\n');
        navigator.clipboard.writeText(text).then(() => {
            const copyBtn = this.popover.querySelector('.popover-copy');
            const originalText = copyBtn.innerText;
            copyBtn.innerText = '✅ Copied!';
            copyBtn.classList.add('btn-success');
            setTimeout(() => {
                copyBtn.innerText = originalText;
                copyBtn.classList.remove('btn-success');
            }, 1000);
        });
    }
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
    window.impactTooltip = new ImpactTooltipManager();
});
