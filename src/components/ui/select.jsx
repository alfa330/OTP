import React, { createContext, useContext, useMemo } from 'react';
import { cn } from './utils';

const SelectContext = createContext(null);

const getItemLabel = (children) => {
    if (typeof children === 'string' || typeof children === 'number') return String(children);
    if (Array.isArray(children)) return children.map(getItemLabel).join('');
    if (React.isValidElement(children)) return getItemLabel(children.props.children);
    return '';
};

const SelectItemMarker = Symbol('SelectItem');

const collectItems = (nodes, acc = []) => {
    React.Children.forEach(nodes, (child) => {
        if (!React.isValidElement(child)) return;
        if (child.type?.[SelectItemMarker]) {
            acc.push({
                value: child.props.value,
                label: getItemLabel(child.props.children)
            });
            return;
        }
        if (child.props?.children) collectItems(child.props.children, acc);
    });
    return acc;
};

export const Select = ({ value, onValueChange, children }) => {
    const items = useMemo(() => collectItems(children), [children]);
    const contextValue = useMemo(
        () => ({ value: value ?? '', onValueChange, items }),
        [value, onValueChange, items]
    );
    return <SelectContext.Provider value={contextValue}>{children}</SelectContext.Provider>;
};

export const SelectTrigger = ({ className = '' }) => {
    const context = useContext(SelectContext);
    return (
        <select
            value={context?.value ?? ''}
            onChange={(event) => context?.onValueChange?.(event.target.value)}
            className={cn(
                'h-10 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-500',
                className
            )}
        >
            {(context?.items || []).map((item) => (
                <option key={item.value} value={item.value}>
                    {item.label}
                </option>
            ))}
        </select>
    );
};

export const SelectValue = () => null;

export const SelectContent = () => null;

export const SelectItem = () => null;
SelectItem[SelectItemMarker] = true;
