import React from 'react';
import FaIcon from './FaIcon';

const ToastContainer = ({ toasts, removeToast, setToasts }) => {
    const handleClose = (id) => {
        if (typeof setToasts === 'function') {
            setToasts(prev => prev.map(toast =>
                toast.id === id ? { ...toast, closing: true } : toast
            ));
        }
        setTimeout(() => removeToast(id), 300); // Match animation duration
    };

    return (
        <div className="fixed bottom-4 right-4 space-y-3 z-[9999]" style={{ zIndex: 9999 }} aria-live="polite">
            {toasts.map(toast => (
                <div
                    key={toast.id}
                    className={`flex items-center p-4 rounded-md shadow-md text-white transition-all duration-300 transform hover:scale-105 hover:shadow-lg border border-opacity-20
                        ${toast.type === 'success' ? 'bg-green-600 border-green-400' : 'bg-red-600 border-red-400'}
                        ${toast.closing ? 'animate-slide-out opacity-0' : 'animate-slide-in'}`}
                >
                    <FaIcon className={`fas ${toast.type === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'} mr-3 text-lg`}></FaIcon>
                    <span className="flex-grow text-sm font-medium">{toast.message}</span>
                    <button 
                        onClick={() => handleClose(toast.id)} 
                        className="ml-4 text-white hover:text-gray-200 focus:outline-none"
                    >
                        <FaIcon className="fas fa-times"></FaIcon>
                    </button>
                    {/* Progress bar */}
                    <div className="absolute bottom-0 left-0 h-1 bg-white bg-opacity-30 w-full">
                        <div 
                            className="h-full bg-white transition-all duration-[5000ms] ease-linear"
                            style={{ width: toast.closing ? '0%' : '0%', animation: 'progress 5s linear forwards' }}
                        ></div>
                    </div>
                </div>
            ))}
        </div>
    );
};

export default ToastContainer;
