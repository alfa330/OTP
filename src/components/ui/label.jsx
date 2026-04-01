import React from 'react';
import { cn } from './utils';

export const Label = ({ className = '', ...props }) => (
    <label className={cn('text-sm font-medium text-slate-700', className)} {...props} />
);
