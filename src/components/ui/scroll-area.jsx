import React from 'react';
import { cn } from './utils';

export const ScrollArea = ({ className = '', ...props }) => (
    <div className={cn('overflow-auto', className)} {...props} />
);
