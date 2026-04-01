import React from 'react';
import { cn } from './utils';

export const Card = ({ className = '', ...props }) => (
    <div className={cn('bg-white', className)} {...props} />
);

export const CardHeader = ({ className = '', ...props }) => (
    <div className={cn('p-6 pb-4', className)} {...props} />
);

export const CardTitle = ({ className = '', ...props }) => (
    <h3 className={cn('text-lg font-semibold text-slate-900', className)} {...props} />
);

export const CardDescription = ({ className = '', ...props }) => (
    <p className={cn('text-sm text-slate-500', className)} {...props} />
);

export const CardContent = ({ className = '', ...props }) => (
    <div className={cn('p-6 pt-0', className)} {...props} />
);
