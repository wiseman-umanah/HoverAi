// ─── Set-of-marks ─────────────────────────────────────────────────────────────
//
// Draws a box and its id number on every accessibility element, on the copy
// of the screenshot sent to the model. The model then answers "element 12"
// instead of guessing coordinates; the id → rect lookup gives exact bounds.
// Digits use a built-in 3×5 bitmap font so no font files are bundled.

use xcap::image::{Rgba, RgbaImage};

use crate::context::ContextElement;

// High-contrast colours, cycled so neighbouring boxes differ.
const PALETTE: [[u8; 3]; 6] = [
    [230, 25, 75],  // red
    [0, 130, 200],  // blue
    [245, 130, 48], // orange
    [145, 30, 180], // purple
    [0, 150, 70],   // green
    [200, 20, 160], // magenta
];

/// 3×5 glyphs for 0–9, one u16 per digit, rows top→bottom, 3 bits per row.
const DIGITS: [u16; 10] = [
    0b111_101_101_101_111, // 0
    0b010_110_010_010_111, // 1
    0b111_001_111_100_111, // 2
    0b111_001_111_001_111, // 3
    0b101_101_111_001_001, // 4
    0b111_100_111_001_111, // 5
    0b111_100_111_101_111, // 6
    0b111_001_010_010_010, // 7
    0b111_101_111_101_111, // 8
    0b111_101_111_001_111, // 9
];

/// Draw marks for `elements` (normalised rects) onto `img` in place.
pub fn draw(img: &mut RgbaImage, elements: &[ContextElement]) {
    let (iw, ih) = (img.width() as f64, img.height() as f64);
    // Font pixel size: the backend re-encodes marked screenshots at ~1280 px,
    // so use 3 px glyph pixels on anything that large to keep ids legible.
    let px = if iw >= 1200.0 { 3 } else { 2 };
    for el in elements {
        let color = PALETTE[(el.id as usize - 1) % PALETTE.len()];
        let x0 = (el.rect.x * iw) as i64;
        let y0 = (el.rect.y * ih) as i64;
        let x1 = ((el.rect.x + el.rect.w) * iw) as i64;
        let y1 = ((el.rect.y + el.rect.h) * ih) as i64;
        stroke_rect(img, x0, y0, x1, y1, color);
        label(img, el.id, x0, y0, px, color);
    }
}

fn put(img: &mut RgbaImage, x: i64, y: i64, c: [u8; 3]) {
    if x >= 0 && y >= 0 && (x as u32) < img.width() && (y as u32) < img.height() {
        img.put_pixel(x as u32, y as u32, Rgba([c[0], c[1], c[2], 255]));
    }
}

fn stroke_rect(img: &mut RgbaImage, x0: i64, y0: i64, x1: i64, y1: i64, c: [u8; 3]) {
    for t in 0..2 {
        for x in x0..=x1 {
            put(img, x, y0 + t, c);
            put(img, x, y1 - t, c);
        }
        for y in y0..=y1 {
            put(img, x0 + t, y, c);
            put(img, x1 - t, y, c);
        }
    }
}

/// Filled tag with white digits at the element's top-left corner (just above
/// it when there's room, so the label doesn't cover the element's text).
fn label(img: &mut RgbaImage, id: u32, x0: i64, y0: i64, px: u32, c: [u8; 3]) {
    let text = id.to_string();
    let glyph_w = 3 * px as i64;
    let glyph_h = 5 * px as i64;
    let pad = px as i64;
    let w = text.len() as i64 * (glyph_w + px as i64) - px as i64 + 2 * pad;
    let h = glyph_h + 2 * pad;
    let top = if y0 - h >= 0 { y0 - h } else { y0 };
    for y in top..top + h {
        for x in x0..x0 + w {
            put(img, x, y, c);
        }
    }
    for (i, ch) in text.bytes().enumerate() {
        let bits = DIGITS[(ch - b'0') as usize];
        let gx = x0 + pad + i as i64 * (glyph_w + px as i64);
        for row in 0..5 {
            for col in 0..3 {
                let bit = 14 - (row * 3 + col);
                if bits >> bit & 1 == 1 {
                    for dy in 0..px as i64 {
                        for dx in 0..px as i64 {
                            put(
                                img,
                                gx + col as i64 * px as i64 + dx,
                                top + pad + row as i64 * px as i64 + dy,
                                [255, 255, 255],
                            );
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::NRect;

    #[test]
    fn draws_box_and_label_inside_image() {
        let mut img = RgbaImage::from_pixel(400, 200, Rgba([0, 0, 0, 255]));
        let el = ContextElement {
            id: 12,
            role: "button".into(),
            name: "Save".into(),
            rect: NRect { x: 0.25, y: 0.5, w: 0.25, h: 0.25 },
            enabled: true,
            focused: false,
        };
        draw(&mut img, &[el]);
        // Box edge at (100, 100) is red (id 12 → palette[11 % 6] = palette[5]).
        assert_eq!(img.get_pixel(150, 100).0[..3], PALETTE[5]);
        // Label sits above the box and contains white digit pixels.
        let white = (100..140).flat_map(|x| (80..100).map(move |y| (x, y)))
            .any(|(x, y)| img.get_pixel(x, y).0[..3] == [255, 255, 255]);
        assert!(white);
    }
}
