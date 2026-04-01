import React, { createContext, useContext, useMemo, useState } from 'react';
import { cn } from './utils';

const TabsContext = createContext(null);

export const Tabs = ({ defaultValue, value, onValueChange, className = '', children }) => {
    const [internalValue, setInternalValue] = useState(defaultValue || '');
    const isControlled = typeof value !== 'undefined';
    const activeValue = isControlled ? value : internalValue;

    const setValue = (nextValue) => {
        if (!isControlled) setInternalValue(nextValue);
        if (typeof onValueChange === 'function') onValueChange(nextValue);
    };

    const contextValue = useMemo(
        () => ({ value: activeValue, setValue }),
        [activeValue]
    );

    return (
        <TabsContext.Provider value={contextValue}>
            <div className={className}>{children}</div>
        </TabsContext.Provider>
    );
};

export const TabsList = ({ className = '', ...props }) => (
    <div className={cn('inline-flex items-center gap-1', className)} {...props} />
);

export const TabsTrigger = ({ value, className = '', children, ...props }) => {
    const context = useContext(TabsContext);
    const active = context?.value === value;
    return (
        <button
            type="button"
            onClick={() => context?.setValue(value)}
            className={cn(
                'px-3 py-1.5 text-sm font-medium transition',
                active ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700',
                className
            )}
            {...props}
        >
            {children}
        </button>
    );
};

export const TabsContent = ({ value, className = '', children, ...props }) => {
    const context = useContext(TabsContext);
    if (!context || context.value !== value) return null;
    return (
        <div className={className} {...props}>
            {children}
        </div>
    );
};
