import React from 'react';
import { cn } from './utils';

const VARIANT_CLASSES = {
    default: 'bg-slate-900 text-white border border-slate-900',
    secondary: 'bg-slate-100 text-slate-700 border border-slate-200',
    outline: 'bg-white text-slate-700 border border-slate-300'
};

export const Badge = ({ className = '', variant = 'default', ...props }) => (
    <span
        className={cn(
            'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
            VARIANT_CLASSES[variant] || VARIANT_CLASSES.default,
            className
        )}
        {...props}
    />
);
