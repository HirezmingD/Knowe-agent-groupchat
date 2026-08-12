/**
 * ToastHost.tsx — 轻提示（component-tree §I · Toast，挂载于 #toastLayer）
 *
 * DOM：#toastLayer > .toast(.warn/.ok/.info)(.out) > 图标 + span
 *
 * 数据：selectNotices —— store 的全局通知通道（无 project_id 的服务器级 error、
 *      回声哨兵告警、纪元重置提示都进这里）。
 *
 * 设计：notices 是只增不减的账本（可追溯），toast 只显示「还没弹过的那些」，
 *      2.4 秒后自己退场（与设计稿 toast() 的时序一致）。
 */

import React, { useEffect, useRef, useState } from 'react';
import { useKnoweStore } from '../store/store';
import { selectNotices } from '../store/selectors';
import { IconAlert } from './icons';

const TOAST_MS = 2400;
const OUT_MS = 320;
//: [v0.44.1 Bug4] 最多同时显示 5 条；第 6 条出现时把最老的挤掉。
const MAX_TOASTS = 5;

//: [v0.44.1 Bug4] 提醒层从右下角移到**屏幕中央偏下**。位置由内联样式接管
//: （盖掉 CSS 里 #toastLayer 的右下定位）；column 自底向上叠，随条数增减自然保持居中偏下。
const LAYER_STYLE: React.CSSProperties = {
  position: 'fixed',
  left: '50%',
  right: 'auto',
  bottom: '15vh',
  transform: 'translateX(-50%)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: 10,
  zIndex: 9000,
  pointerEvents: 'none',
  maxWidth: '92vw',
};

interface Shown {
  key: string;
  text: string;
  out: boolean;
}

export const ToastHost: React.FC = () => {
  const notices = useKnoweStore(selectNotices);
  const seenRef = useRef(new Map<string, string>());
  const timersRef = useRef(new Map<string, ReturnType<typeof setTimeout>[]>());
  const [shown, setShown] = useState<Shown[]>([]);

  useEffect(() => {
    const scheduleRemoval = (key: string): void => {
      for (const timer of timersRef.current.get(key) ?? []) clearTimeout(timer);
      const outTimer = setTimeout(() => {
        setShown((prev) => prev.map((toast) => (
          toast.key === key ? { ...toast, out: true } : toast
        )));
        const removeTimer = setTimeout(() => {
          setShown((prev) => prev.filter((toast) => toast.key !== key));
          timersRef.current.delete(key);
        }, OUT_MS);
        timersRef.current.set(key, [removeTimer]);
      }, TOAST_MS);
      timersRef.current.set(key, [outTimer]);
    };

    notices.forEach((notice, index) => {
      const key = notice.id || `notice-${index}`;
      const signature = `${notice.timestamp}\u0000${notice.message}`;
      if (seenRef.current.get(key) === signature) return;
      seenRef.current.set(key, signature);
      setShown((prev) => {
        const existing = prev.find((toast) => toast.key === key);
        if (existing) {
          return prev.map((toast) => (
            toast.key === key ? { ...toast, text: notice.message, out: false } : toast
          ));
        }
        return [...prev, { key, text: notice.message, out: false }].slice(-MAX_TOASTS);
      });
      scheduleRemoval(key);
    });
  }, [notices]);

  useEffect(() => () => {
    for (const timers of timersRef.current.values()) {
      for (const timer of timers) clearTimeout(timer);
    }
    timersRef.current.clear();
  }, []);

  return (
    <div id="toastLayer" style={LAYER_STYLE}>
      {shown.map((t) => (
        <div key={t.key} className={'toast warn' + (t.out ? ' out' : '')} role="status">
          <IconAlert />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
};

export default ToastHost;
