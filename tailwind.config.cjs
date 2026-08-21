/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './call_evaluation.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      keyframes: {
        'slide-in': {
          '0%': { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' }
        },
        'slide-out': {
          '0%': { transform: 'translateX(0)', opacity: '1' },
          '100%': { transform: 'translateX(100%)', opacity: '0' }
        },
        progress: {
          '0%': { width: '100%' },
          '100%': { width: '0%' }
        },
        'fade-in-out': {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '10%': { opacity: '1', transform: 'scale(1)' },
          '90%': { opacity: '1', transform: 'scale(1)' },
          '100%': { opacity: '0', transform: 'scale(0.95)' }
        },
        dropdown: {
          '0%': { transform: 'scaleY(0)', opacity: '0' },
          '100%': { transform: 'scaleY(1)', opacity: '1' }
        },
        // Звон: качание вокруг верхней точки крепления, затухающее к концу —
        // ровно так качается настоящий колокол, поэтому амплитуда убывает.
        'bell-ring': {
          '0%, 100%': { transform: 'rotate(0deg)' },
          '10%': { transform: 'rotate(-14deg)' },
          '20%': { transform: 'rotate(12deg)' },
          '30%': { transform: 'rotate(-10deg)' },
          '40%': { transform: 'rotate(8deg)' },
          '50%': { transform: 'rotate(-6deg)' },
          '60%': { transform: 'rotate(4deg)' },
          '70%': { transform: 'rotate(-2deg)' },
          '80%': { transform: 'rotate(1deg)' }
        },
        'dropdown-reverse': {
          '0%': { transform: 'scaleY(1)', opacity: '1' },
          '100%': { transform: 'scaleY(0)', opacity: '0' }
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' }
        },
        /* Раскрытие карточки на всю область: лёгкий рост от 0.97, а не выезд
           со стороны — так это читается как «карточка развернулась», а не
           «приехал другой экран». */
        cardOpen: {
          '0%': { opacity: '0', transform: 'scale(0.97)' },
          '100%': { opacity: '1', transform: 'scale(1)' }
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.95) translateY(-10px)' },
          '100%': { opacity: '1', transform: 'scale(1) translateY(0)' }
        },
        scaleOut: {
          '0%': { opacity: '1', transform: 'scale(1) translateY(0)' },
          '100%': { opacity: '0', transform: 'scale(0.95) translateY(-10px)' }
        }
      },
      animation: {
        'slide-in': 'slide-in 0.2s ease-out',
        'slide-out': 'slide-out 0.3s ease-in',
        progress: 'progress 5s linear forwards',
        'fade-in-out': 'fade-in-out 5s ease-in-out forwards',
        dropdown: 'dropdown 0.2s ease-out forwards',
        'bell-ring': 'bell-ring 0.9s ease-in-out',
        'dropdown-reverse': 'dropdown-reverse 0.2s ease-in forwards',
        'fade-in': 'fadeIn 0.3s ease-out forwards',
        'scale-in': 'scaleIn 0.2s ease-out forwards',
        'card-open': 'cardOpen 0.28s cubic-bezier(0.4, 0, 0.2, 1) both',
        'scale-out': 'scaleOut 0.2s ease-in forwards'
      }
    }
  },
  plugins: []
};
