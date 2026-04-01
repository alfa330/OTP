import React from 'react';
import { cn } from './utils';

export const Textarea = ({ className = '', ...props }) => (
    <textarea
        className={cn(
            'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-500',
            className
        )}
        {...props}
    />
);
