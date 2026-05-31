# Panel Resize System - Code Level Guide

## Overview
All panels are now **fully resizable** regardless of their collapse state. Dragging any splitter will automatically expand collapsed panels.

---

## 1. **Splitter Types**

### Vertical Splitters (Column resizing)
**HTML elements:** `<div class="splitter" data-left="..." data-right="..."></div>`

**Locations:**
- Between Outline (`#left`) and Markdown (`#middle`)
- Between Markdown (`#middle`) and Chat (`#chat`)
- Between Chat (`#chat`) and PDF (`#right`)
- Between PDF (`#right`) and Graph (`#graph`)

**Code:** [viewer.html:1407-1475](viewer.html#L1407-L1475)

```javascript
// When mousedown on splitter:
// 1. Check if left or right panel is collapsed
// 2. On mousemove: auto-expand collapsed panels
// 3. Update flexBasis for visible panels
// 4. On mouseup: keep panels expanded (user can manually collapse if needed)
```

### Horizontal Splitters (Row resizing)
**HTML element:** `<div class="hsplitter" data-top="..." data-bottom="..."></div>`

**Location:** Inside `#middle` between markdown and summary sections

**Code:** [viewer.html:1373-1425](viewer.html#L1373-L1425)

Same logic as vertical splitters, but operates on `height` instead of `width`.

### Right Edge Handle
**HTML element:** `<div id="right-resize-handle"></div>`

**Code:** [viewer.html:1449-1492](viewer.html#L1449-L1492)

Allows dragging the far-right edge of the PDF panel to expand/shrink it.

---

## 2. **Panel States**

### Normal State
```html
<section id="chat" class="panel" style="flex-basis:360px;">
```
- Has `flex-basis` set (fixed width)
- Can be collapsed by clicking collapse button
- Can be resized by dragging splitter

### Collapsed State
```html
<section id="chat" class="panel collapsed" style="flex-basis:360px;">
```
- Has `.collapsed` class added
- Width becomes 28px (only shows vertical label)
- Content hidden with `overflow: hidden`
- **Now draggable** — dragging will expand it

### Dragging State
```javascript
// Temporary class during drag
panel.classList.add("_dragging_expanded");
// After drag ends, class is removed (panel stays expanded)
```

---

## 3. **Key Code Patterns**

### Detecting Collapsed State
```javascript
const isCollapsed = panel.classList.contains("collapsed");
```

### Auto-Expand on Drag
```javascript
if (lCollapsed && !left.classList.contains("_dragging_expanded")) {
  left.classList.remove("collapsed");
  left.classList.add("_dragging_expanded");
}
```

### Updating Panel Size During Drag
```javascript
left.style.flex = "0 0 auto";  // Convert from flex-fill to fixed
left.style.flexBasis = newL + "px";  // Set explicit width
```

### Cleanup After Drag
```javascript
left.classList.remove("_dragging_expanded");  // Remove temp class
// Panel stays expanded unless user manually collapses it
```

---

## 4. **CSS Classes**

| Class | Effect | Modified When |
|-------|--------|---------------|
| `.panel` | Base panel styling | Always present |
| `.flex-fill` | Panel grows to fill available space | Drag converts to fixed sizing |
| `.collapsed` | Panel width: 28px, content hidden | Click collapse button OR drag removes it |
| `._dragging_expanded` | Temporary class during drag | Added/removed during drag only |
| `.dragging` | Splitter highlight during drag | Added to splitter element |

---

## 5. **Resize Logic Flow**

```
User clicks splitter
    ↓
Save initial state (collapsed status, rect sizes)
    ↓
On mousemove:
  ├─ Check if left panel collapsed
  ├─ If yes → remove .collapsed class → recalculate rect
  ├─ Check if right panel collapsed  
  ├─ If yes → remove .collapsed class → recalculate rect
  ├─ Calculate new widths (left + right = total)
  ├─ Update flexBasis for both panels
    ↓
On mouseup:
  ├─ Remove splitter dragging state
  ├─ Remove temporary _dragging_expanded class
  ├─ Panels remain expanded (unless manually collapsed later)
```

---

## 6. **Panel Layout Reference**

| Panel | ID | Min Width | Default |
|-------|----|-----------|----|
| Outline | `#left` | 80px | 220px |
| Markdown + Summary | `#middle` | 80px | flex-fill |
| RAG Chat | `#chat` | 80px | 360px |
| PDF | `#right` | 80px | flex-fill |
| Knowledge Graph | `#graph` | 80px | flex-fill |

---

## 7. **Keyboard Shortcuts** (unchanged)

| Key | Action |
|-----|--------|
| Ctrl+2 | Toggle Outline panel |
| Ctrl+3 | Toggle Markdown panel |
| Ctrl+4 | Toggle Chat panel |
| Ctrl+5 | Toggle PDF panel |
| Ctrl+6 | Toggle Graph panel |
| Ctrl+G | Collapse all except Graph |

---

## 8. **Example: Drag Outline Right to Expand Markdown**

```
1. Outline (#left) is collapsed to 28px width
2. User hovers over splitter between #left and #middle
3. User clicks and drags RIGHT
4. Splitter detects #left is collapsed
5. #left.classList.remove("collapsed")
6. #left expands to show full outline content
7. #middle shrinks to accommodate
8. User releases mouse
9. Both panels stay in new sizes
```

---

## 9. **Technical Notes**

### Why not use `_dragging_expanded`?
- Distinguishes between "collapsed by user" vs "expanded during drag"
- Allows cleanup without forcing collapsed state back
- Temporary marker that's removed after drag ends

### Why recalculate `rRect` after uncollapsing?
```javascript
left.classList.remove("collapsed");
lRect = left.getBoundingClientRect();  // Must recalculate!
```
- Collapsed panels report width of 28px
- After removal of `.collapsed`, actual content width changes
- Must get fresh measurements for smooth dragging

### Min width protection
```javascript
const newL = Math.max(80, lRect.width + dx);  // Never go below 80px
if (newL <= 80 && dx < 0) return;  // Skip this frame if clamped
```
- Prevents panels from becoming too small to interact with
- Smooth drag by skipping frames that would violate constraints

---

## 10. **Customization**

### Change minimum panel width
```javascript
const newL = Math.max(100, lRect.width + dx);  // Change 80 to 100
```

### Auto-collapse after drag (if desired)
```javascript
function onUp() {
  left.classList.remove("_dragging_expanded");
  if (shouldCollapse) left.classList.add("collapsed");  // Add this
  // ...rest of onUp
}
```

### Persist panel sizes to localStorage
```javascript
// In onUp():
localStorage.setItem(`panel-${left.id}`, left.style.flexBasis);
```

---

## Files Modified
- `viewer.html` — Lines 1373-1492 (splitter logic)

## Server
- `serve.py` — No changes (purely frontend feature)
