import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const TOOLTIP_SELECTOR = '[data-schedule-tooltip]';
const SHOW_DELAY_MS = 250;
const VIEWPORT_GAP = 12;
const ANCHOR_GAP = 9;
const ARROW_EDGE_GAP = 12;

const useSafeLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

const getTooltipAnchor = (target) => {
    if (typeof Element === 'undefined' || !(target instanceof Element)) return null;
    return target.closest(TOOLTIP_SELECTOR);
};

const getTooltipText = (anchor) => (
    String(anchor?.getAttribute?.('data-schedule-tooltip') || '').trim()
);

const clamp = (value, min, max) => Math.min(Math.max(value, min), Math.max(min, max));

function ScheduleTimelineTooltip() {
    const tooltipRef = useRef(null);
    const activeAnchorRef = useRef(null);
    const hoveredAnchorRef = useRef(null);
    const focusedAnchorRef = useRef(null);
    const pendingAnchorRef = useRef(null);
    const showTimerRef = useRef(null);
    const positionFrameRef = useRef(null);
    const [activeTooltip, setActiveTooltip] = useState(null);
    const [position, setPosition] = useState(null);

    const hideTooltip = useCallback(() => {
        activeAnchorRef.current = null;
        setActiveTooltip(null);
        setPosition(null);
    }, []);

    const cancelPendingShow = useCallback(() => {
        if (showTimerRef.current != null) {
            clearTimeout(showTimerRef.current);
            showTimerRef.current = null;
        }
        pendingAnchorRef.current = null;
    }, []);

    const showTooltip = useCallback((anchor) => {
        cancelPendingShow();
        const text = getTooltipText(anchor);
        if (!anchor?.isConnected || !text) {
            hideTooltip();
            return;
        }

        activeAnchorRef.current = anchor;
        setActiveTooltip((current) => (
            current?.anchor === anchor && current.text === text
                ? current
                : { anchor, text }
        ));
    }, [cancelPendingShow, hideTooltip]);

    const scheduleTooltip = useCallback((anchor) => {
        const text = getTooltipText(anchor);
        if (!anchor?.isConnected || !text) {
            cancelPendingShow();
            hideTooltip();
            return;
        }
        if (activeAnchorRef.current === anchor || pendingAnchorRef.current === anchor) return;

        cancelPendingShow();
        if (activeAnchorRef.current && activeAnchorRef.current !== anchor) {
            hideTooltip();
        }

        pendingAnchorRef.current = anchor;
        showTimerRef.current = setTimeout(() => {
            showTimerRef.current = null;
            pendingAnchorRef.current = null;
            if (hoveredAnchorRef.current === anchor && anchor.isConnected) {
                showTooltip(anchor);
            }
        }, SHOW_DELAY_MS);
    }, [cancelPendingShow, hideTooltip, showTooltip]);

    const fallBackFrom = useCallback((anchor) => {
        if (activeAnchorRef.current !== anchor) return;
        const fallbackAnchor = hoveredAnchorRef.current || focusedAnchorRef.current;
        if (fallbackAnchor && fallbackAnchor !== anchor) {
            showTooltip(fallbackAnchor);
        } else if (!fallbackAnchor) {
            hideTooltip();
        }
    }, [hideTooltip, showTooltip]);

    const updatePosition = useCallback(() => {
        const anchor = activeAnchorRef.current;
        const tooltip = tooltipRef.current;
        if (!anchor?.isConnected || !tooltip || typeof window === 'undefined') {
            if (anchor && !anchor.isConnected) hideTooltip();
            return;
        }

        const text = getTooltipText(anchor);
        if (!text) {
            hideTooltip();
            return;
        }

        setActiveTooltip((current) => (
            current?.anchor === anchor && current.text !== text
                ? { anchor, text }
                : current
        ));

        const anchorRect = anchor.getBoundingClientRect();
        const tooltipRect = tooltip.getBoundingClientRect();
        const viewportWidth = document.documentElement?.clientWidth || window.innerWidth;
        const viewportHeight = document.documentElement?.clientHeight || window.innerHeight;
        const tooltipWidth = tooltipRect.width;
        const tooltipHeight = tooltipRect.height;
        const spaceAbove = anchorRect.top - VIEWPORT_GAP;
        const spaceBelow = viewportHeight - anchorRect.bottom - VIEWPORT_GAP;
        const placement = spaceAbove >= tooltipHeight + ANCHOR_GAP || spaceAbove >= spaceBelow
            ? 'top'
            : 'bottom';
        const anchorCenter = anchorRect.left + (anchorRect.width / 2);
        const halfTooltipWidth = tooltipWidth / 2;
        const left = clamp(
            anchorCenter,
            VIEWPORT_GAP + halfTooltipWidth,
            viewportWidth - VIEWPORT_GAP - halfTooltipWidth
        );
        const preferredTop = placement === 'top'
            ? anchorRect.top - ANCHOR_GAP - tooltipHeight
            : anchorRect.bottom + ANCHOR_GAP;
        const top = clamp(
            preferredTop,
            VIEWPORT_GAP,
            viewportHeight - VIEWPORT_GAP - tooltipHeight
        );
        const tooltipLeftEdge = left - halfTooltipWidth;
        const arrowLeft = clamp(
            anchorCenter - tooltipLeftEdge,
            ARROW_EDGE_GAP,
            tooltipWidth - ARROW_EDGE_GAP
        );

        setPosition({ left, top, placement, arrowLeft });
    }, [hideTooltip]);

    const schedulePositionUpdate = useCallback(() => {
        if (typeof window === 'undefined' || positionFrameRef.current != null) return;
        positionFrameRef.current = window.requestAnimationFrame(() => {
            positionFrameRef.current = null;
            updatePosition();
        });
    }, [updatePosition]);

    useEffect(() => {
        if (typeof document === 'undefined') return undefined;

        const handlePointerOver = (event) => {
            const anchor = getTooltipAnchor(event.target);
            const previousAnchor = getTooltipAnchor(event.relatedTarget);
            if (!anchor || anchor === previousAnchor) return;
            hoveredAnchorRef.current = anchor;
            scheduleTooltip(anchor);
        };

        const handlePointerOut = (event) => {
            const anchor = getTooltipAnchor(event.target);
            if (!anchor) return;
            const nextAnchor = getTooltipAnchor(event.relatedTarget);
            if (nextAnchor === anchor) return;

            if (hoveredAnchorRef.current === anchor) {
                hoveredAnchorRef.current = nextAnchor;
            }
            if (pendingAnchorRef.current === anchor) {
                cancelPendingShow();
            }
            if (nextAnchor) {
                scheduleTooltip(nextAnchor);
            } else {
                fallBackFrom(anchor);
            }
        };

        const handleFocusIn = (event) => {
            const anchor = getTooltipAnchor(event.target);
            if (!anchor) return;
            focusedAnchorRef.current = anchor;
            showTooltip(anchor);
        };

        const handleFocusOut = (event) => {
            const anchor = getTooltipAnchor(event.target);
            if (!anchor) return;
            const nextAnchor = getTooltipAnchor(event.relatedTarget);
            if (nextAnchor === anchor) return;

            if (focusedAnchorRef.current === anchor) {
                focusedAnchorRef.current = nextAnchor;
            }
            if (nextAnchor) {
                showTooltip(nextAnchor);
            } else {
                fallBackFrom(anchor);
            }
        };

        document.addEventListener('pointerover', handlePointerOver);
        document.addEventListener('pointerout', handlePointerOut);
        document.addEventListener('focusin', handleFocusIn);
        document.addEventListener('focusout', handleFocusOut);

        return () => {
            document.removeEventListener('pointerover', handlePointerOver);
            document.removeEventListener('pointerout', handlePointerOut);
            document.removeEventListener('focusin', handleFocusIn);
            document.removeEventListener('focusout', handleFocusOut);
        };
    }, [cancelPendingShow, fallBackFrom, scheduleTooltip, showTooltip]);

    useSafeLayoutEffect(() => {
        if (!activeTooltip) return;
        updatePosition();
    }, [activeTooltip, updatePosition]);

    useEffect(() => {
        if (!activeTooltip || typeof window === 'undefined') return undefined;

        window.addEventListener('scroll', schedulePositionUpdate, true);
        window.addEventListener('resize', schedulePositionUpdate);
        return () => {
            window.removeEventListener('scroll', schedulePositionUpdate, true);
            window.removeEventListener('resize', schedulePositionUpdate);
        };
    }, [activeTooltip, schedulePositionUpdate]);

    useEffect(() => () => {
        cancelPendingShow();
        if (positionFrameRef.current != null && typeof window !== 'undefined') {
            window.cancelAnimationFrame(positionFrameRef.current);
        }
    }, [cancelPendingShow]);

    if (!activeTooltip || typeof document === 'undefined' || !document.body) return null;

    const placement = position?.placement || 'top';

    return createPortal(
        <div
            ref={tooltipRef}
            role="tooltip"
            className="pointer-events-none fixed z-[9999] w-max max-w-[min(20rem,calc(100vw-1.5rem))] whitespace-pre-line break-words rounded-xl border border-white/10 bg-slate-950/95 px-3 py-2 text-[12px] font-medium leading-4 text-white shadow-[0_12px_32px_rgba(15,23,42,0.3)] backdrop-blur-md"
            style={{
                left: position?.left ?? 0,
                top: position?.top ?? 0,
                transform: 'translateX(-50%)',
                visibility: position ? 'visible' : 'hidden'
            }}
        >
            {activeTooltip.text}
            <span
                aria-hidden="true"
                className={`absolute h-2.5 w-2.5 -translate-x-1/2 rotate-45 bg-slate-950/95 ${
                    placement === 'top'
                        ? '-bottom-[5px] border-b border-r border-white/10'
                        : '-top-[5px] border-l border-t border-white/10'
                }`}
                style={{ left: position?.arrowLeft ?? '50%' }}
            />
        </div>,
        document.body
    );
}

export default ScheduleTimelineTooltip;
