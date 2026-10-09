use serde::Serialize;
use std::collections::BTreeMap;
use wasm_bindgen::prelude::*;
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Key {
    id: String,
    time: i64,
    value: f64,
    segment_to_next: &'static str,
    tangent_mode: &'static str,
}
#[derive(Serialize)]
struct Channel {
    keys: Vec<Key>,
}
fn preset(name: &str, duration: i64) -> BTreeMap<String, Channel> {
    let mut channels = BTreeMap::new();
    if duration <= 8 || name == "none" {
        return channels;
    }
    let entrance = (duration / 5).clamp(4, 60_000);
    let mut add = |path: &str, values: Vec<(i64, f64)>| {
        channels.insert(
            path.to_owned(),
            Channel {
                keys: values
                    .into_iter()
                    .enumerate()
                    .map(|(i, (time, value))| Key {
                        id: format!("preset-{path}-{i}"),
                        time,
                        value,
                        segment_to_next: "linear",
                        tangent_mode: "auto",
                    })
                    .collect(),
            },
        );
    };
    match name {
        "fade" => add(
            "opacity",
            vec![
                (0, 0.0),
                (entrance, 1.0),
                (duration - entrance, 1.0),
                (duration, 0.0),
            ],
        ),
        "left" | "right" | "up" | "down" => {
            let (path, start) = match name {
                "left" => ("transform.positionX", -240.0),
                "right" => ("transform.positionX", 240.0),
                "up" => ("transform.positionY", 240.0),
                _ => ("transform.positionY", -240.0),
            };
            add(path, vec![(0, start), (entrance, 0.0)]);
            add("opacity", vec![(0, 0.0), (entrance, 1.0)]);
        }
        "pop" => {
            for path in ["transform.scaleX", "transform.scaleY"] {
                add(
                    path,
                    vec![(0, 0.05), (entrance * 3 / 4, 1.12), (entrance, 1.0)],
                );
            }
        }
        "spin" => {
            add("transform.rotate", vec![(0, -180.0), (entrance, 0.0)]);
            add("opacity", vec![(0, 0.0), (entrance, 1.0)]);
        }
        "pulse" => {
            for path in ["transform.scaleX", "transform.scaleY"] {
                add(
                    path,
                    vec![
                        (0, 1.0),
                        (duration / 4, 1.12),
                        (duration / 2, 1.0),
                        (duration * 3 / 4, 1.12),
                        (duration, 1.0),
                    ],
                );
            }
        }
        _ => {}
    }
    channels
}
#[wasm_bindgen(js_name = textAnimationPreset)]
pub fn text_animation_preset(name: &str, duration: i64) -> JsValue {
    // Plain JS objects match the editor's channel schema, rather than JS Maps.
    preset(name, duration)
        .serialize(&serde_wasm_bindgen::Serializer::json_compatible())
        .unwrap()
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn presets_fit_duration_and_are_finite() {
        for duration in [0, 1, 8, 9, 120000, 600000] {
            for name in [
                "none", "fade", "left", "right", "up", "down", "pop", "spin", "pulse", "unknown",
            ] {
                for channel in preset(name, duration).values() {
                    assert!(
                        channel
                            .keys
                            .iter()
                            .all(|k| k.time >= 0 && k.time <= duration && k.value.is_finite())
                    );
                    assert!(channel.keys.windows(2).all(|p| p[0].time < p[1].time));
                }
            }
        }
    }
}
