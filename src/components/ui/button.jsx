import React from 'react';
import { cn } from './utils';

const VARIANT_CLASSES = {
    default: 'bg-slate-900 text-white hover:bg-slate-800 border border-slate-900',
    outline: 'bg-white text-slate-800 hover:bg-slate-50 border border-slate-300',
    secondary: 'bg-slate-100 text-slate-800 hover:bg-slate-200 border border-slate-100',
    ghost: 'bg-transparent text-slate-700 hover:bg-slate-100 border border-transparent'
};

const BASE_CLASSES =
    'inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50';

export const Button = ({ className = '', variant = 'default', asChild = false, children, ...props }) => {
    const classes = cn(BASE_CLASSES, VARIANT_CLASSES[variant] || VARIANT_CLASSES.default, className);

    if (asChild && React.isValidElement(children)) {
        return React.cloneElement(children, {
            className: cn(classes, children.props.className || '')
        });
    }

    return (
        <button className={classes} {...props}>
            {children}
        </button>
    );
};
