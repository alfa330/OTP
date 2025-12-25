// Полноэкранная гирлянда с динамической адаптацией и улучшенными эффектами
document.addEventListener('DOMContentLoaded', function() {
  try {
    // Создаем контейнер гирлянды
    const container = document.createElement('div');
    container.className = 'garland';

    // Создаем нить
    const string = document.createElement('div');
    string.className = 'string';
    container.appendChild(string);

    // Расширенная палитра цветов
    const colors = [
      '#FF4D4F',  // Красный
      '#FFD23F',  // Желтый
      '#4ADE80',  // Зеленый
      '#60A5FA',  // Синий
      '#A78BFA',  // Фиолетовый
      '#FB7185',  // Розовый
      '#F97316',  // Оранжевый
      '#14B8A6',  // Бирюзовый
      '#EC4899',  // Маджента
      '#FACC15'   // Золотой
    ];

    // Функция для создания гирлянды
    function createGarland() {
      // Очищаем старые лампочки (кроме нити)
      const oldBulbs = container.querySelectorAll('.garland-bulb');
      oldBulbs.forEach(bulb => bulb.remove());

      // Вычисляем количество лампочек на основе ширины экрана
      const screenWidth = window.innerWidth;
      const bulbSpacing = 45; // Расстояние между лампочками в пикселях
      const count = Math.max(12, Math.min(50, Math.floor(screenWidth / bulbSpacing)));

      // Создаем лампочки
      for (let i = 0; i < count; i++) {
        const bulb = document.createElement('div');
        bulb.className = 'garland-bulb';
        
        // Выбираем цвет
        bulb.style.background = colors[i % colors.length];
        bulb.style.color = colors[i % colors.length]; // Для box-shadow currentColor

        // Позиционирование
        const t = i / (count - 1); // Нормализованная позиция (0 до 1)
        
        // Горизонтальная позиция с небольшими отступами
        const margin = 30;
        const x = margin + t * (screenWidth - 2 * margin);
        
        // Вертикальная позиция с красивой волной
        // Используем комбинацию синусоид для более интересной формы
        const wave1 = Math.sin(t * Math.PI * 2.5) * 35;
        const wave2 = Math.sin(t * Math.PI * 1.2 + Math.PI/4) * 15;
        const y = 50 + wave1 + wave2;

        bulb.style.left = Math.round(x) + 'px';
        bulb.style.top = Math.round(y) + 'px';
        
        // Случайная задержка анимации для эффекта живого мерцания
        const delay = (Math.random() * 3).toFixed(2);
        bulb.style.animationDelay = delay + 's';
        
        // Небольшая вариация начальной непрозрачности
        bulb.style.opacity = (0.85 + Math.random() * 0.15).toFixed(2);

        container.appendChild(bulb);
      }
    }

    // Создаем начальную гирлянду
    createGarland();

    // Пересоздаем гирлянду при изменении размера окна
    let resizeTimer;
    window.addEventListener('resize', function() {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function() {
        createGarland();
      }, 250);
    });

    // Добавляем контейнер на страницу
    document.body.appendChild(container);

    // Опциональный эффект: добавляем легкое покачивание всей гирлянде
    let swayOffset = 0;
    function animateSway() {
      swayOffset += 0.01;
      const sway = Math.sin(swayOffset) * 2;
      string.style.transform = `translateY(${sway}px)`;
      requestAnimationFrame(animateSway);
    }
    
    // Запускаем анимацию покачивания (можно закомментировать, если не нужно)
    // animateSway();

  } catch(e) { 
    console.error('Garland initialization error:', e); 
  }
});

// Экспорт для использования в модулях (опционально)
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { init: () => console.log('Garland initialized') };
}