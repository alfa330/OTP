/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './call_evaluation.html', './trainers.html', './src/**/*.{js,jsx,ts,tsx}'],
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
        },
        /* Второй уровень ВНУТРИ модалки — как переход между экранами в
           «Настройках» iOS: вперёд экран приезжает справа, назад — слева.
           Сдвиг маленький (14px), а не на всю ширину: полный выезд внутри
           прокручиваемого тела окна пришлось бы прятать под overflow и он
           читался бы как «уехала страница», а не «открылась подробность». */
        pushIn: {
          '0%': { opacity: '0', transform: 'translateX(14px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' }
        },
        popIn: {
          '0%': { opacity: '0', transform: 'translateX(-14px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' }
        },
        /* Окна портала на компьютере (IosModal, FullscreenSheet): затемнение
           проявляется, панель приподнимается из 0.97 — та же кривая, что у
           IOS_MODAL_MOTION в ui/ios.jsx (быстрый старт, мягкое приземление), но
           чуть дольше: мгновенное появление читалось как рывок. Уход короче
           прихода — закрытие не должно заставлять ждать. */
        windowDimIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' }
        },
        windowDimOut: {
          '0%': { opacity: '1' },
          '100%': { opacity: '0' }
        },
        windowIn: {
          '0%': { opacity: '0', transform: 'translateY(12px) scale(0.97)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' }
        },
        windowOut: {
          '0%': { opacity: '1', transform: 'translateY(0) scale(1)' },
          '100%': { opacity: '0', transform: 'translateY(8px) scale(0.98)' }
        },
        /* Выпадашки и календари: лёгкое опускание к месту, а не вспышка. Без
           масштаба: календари замеряют настоящую высоту панели, чтобы не уйти за
           край экрана, а сжатая на первом кадре панель замерилась бы меньше. */
        popoverIn: {
          '0%': { opacity: '0', transform: 'translateY(-4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
        },
        /* Панель, появившаяся внутри окна (отмена, возврат, передача этапа). */
        reveal: {
          '0%': { opacity: '0', transform: 'translateY(-4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
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
        'scale-out': 'scaleOut 0.2s ease-in forwards',
        /* Кривая та же, что у раскрытия модалки (IOS_MODAL_MOTION): быстрый
           старт, мягкое приземление — характер macOS. */
        'push-in': 'pushIn 0.22s cubic-bezier(0.16, 1, 0.3, 1) both',
        'pop-in': 'popIn 0.22s cubic-bezier(0.16, 1, 0.3, 1) both',
        /* Приход — с заполнением «backwards», а не «both»: по окончании у панели не
           должно оставаться transform, иначе она навсегда стала бы опорой для
           position: fixed внутри себя. Уход — «forwards»: окно гаснет и
           снимается, обратно не мигает. Длительность ухода окна (0.2 с) равна
           WINDOW_LEAVE_MS в ui/ios.jsx и FullscreenSheet.jsx — сторожит тест. */
        'window-dim-in': 'windowDimIn 0.24s ease-out backwards',
        'window-dim-out': 'windowDimOut 0.2s ease-in forwards',
        'window-in': 'windowIn 0.32s cubic-bezier(0.16, 1, 0.3, 1) backwards',
        'window-out': 'windowOut 0.2s cubic-bezier(0.4, 0, 1, 1) forwards',
        'popover-in': 'popoverIn 0.2s cubic-bezier(0.16, 1, 0.3, 1) backwards',
        reveal: 'reveal 0.24s cubic-bezier(0.16, 1, 0.3, 1) backwards',
        'fade-in-soft': 'fadeIn 0.22s ease-out backwards'
      }
    }
  },
  plugins: []
};
