use serde::Serialize;

#[derive(Serialize, Debug, PartialEq)]
pub struct FadePoint {
    pub time: i64,
    pub value: f64,
}

pub fn fade_points(
    duration: i64,
    requested: i64,
    fade_in: bool,
    fade_out: bool,
    opacity: f64,
) -> Vec<FadePoint> {
    let duration = duration.max(0);
    if duration == 0 || (!fade_in && !fade_out) {
        return vec![];
    }
    let max_edge = if fade_in && fade_out {
        duration / 2
    } else {
        duration
    };
    let edge = requested.clamp(1, max_edge.max(1));
    let opacity = opacity.clamp(0.0, 1.0);
    let mut points = vec![];
    if fade_in {
        points.push(FadePoint {
            time: 0,
            value: 0.0,
        });
        points.push(FadePoint {
            time: edge,
            value: opacity,
        });
    }
    if fade_out {
        let start = duration.saturating_sub(edge);
        if points.last().is_none_or(|p| p.time != start) {
            points.push(FadePoint {
                time: start,
                value: opacity,
            });
        }
        points.push(FadePoint {
            time: duration,
            value: 0.0,
        });
    }
    points
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn short_clip_clamps_edges_without_duplicate_time() {
        assert_eq!(
            fade_points(100, 200, true, true, 0.8),
            vec![
                FadePoint {
                    time: 0,
                    value: 0.0
                },
                FadePoint {
                    time: 50,
                    value: 0.8
                },
                FadePoint {
                    time: 100,
                    value: 0.0
                }
            ]
        );
    }
    #[test]
    fn one_edge_does_not_change_other_edge() {
        assert_eq!(
            fade_points(100, 20, false, true, 1.0),
            vec![
                FadePoint {
                    time: 80,
                    value: 1.0
                },
                FadePoint {
                    time: 100,
                    value: 0.0
                }
            ]
        );
        assert!(fade_points(0, 20, true, true, 1.0).is_empty());
    }
}
