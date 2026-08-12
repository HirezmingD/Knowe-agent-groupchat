/**
 * MSetSelect.tsx — [v1.0.24.1] 设置页通用自定义下拉
 *
 * 与 ModelBindingModule 的「厂商/模型」下拉（.mset-select-list）完全同款交互与样式：
 *   · trigger：.field.mset-field.mset-select-trigger（显示当前值 + ▾）
 *   · 弹层：.mset-select-list（绝对定位，点击外部关闭）
 *   · 选项：.mset-select-opt（选中项 .sel 高亮）
 *
 * 用于替换设置页里原生 <select className="field">（外观模式/字号/审批超时/测试目标），
 * 使设置内所有下拉视觉与交互统一。样式定义在 settings-view.css（.mset-* 系列）。
 */
import React, { useEffect, useRef, useState } from 'react';

export interface MSetOption<T extends string | number> {
  value: T;
  label: string;
}

interface MSetSelectProps<T extends string | number> {
  value: T;
  options: MSetOption<T>[];
  onChange: (v: T) => void;
  ariaLabel: string;
  disabled?: boolean;
}

export default function MSetSelect<T extends string | number>({
  value, options, onChange, ariaLabel, disabled,
}: MSetSelectProps<T>): React.ReactElement {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // 点击组件外部 → 关闭弹层（与 ModelBindingModule 同款）
  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: MouseEvent): void => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [open]);

  const current = options.find((o) => o.value === value);

  return (
    <div className="mset-select" ref={boxRef}>
      <button
        type="button"
        className="field mset-field mset-select-trigger"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
      >
        <span className="mset-select-label">{current ? current.label : ''}</span>
        <span className="mset-select-caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="mset-select-list" role="listbox" aria-label={ariaLabel}>
          {options.map((o) => (
            <div
              key={String(o.value)}
              role="option"
              aria-selected={value === o.value}
              className={'mset-select-opt' + (value === o.value ? ' sel' : '')}
              onClick={() => { onChange(o.value); setOpen(false); }}
            >
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
