import React from 'react';
import { cn } from './utils';

export const Separator = ({ className = '' }) => (
    <div className={cn('h-px w-full bg-slate-200', className)} />
);
