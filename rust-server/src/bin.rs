use cpal::traits::{DeviceTrait, HostTrait};
fn main() {
    let host = cpal::default_host();
    let device = host.default_output_device().unwrap();
    let config = device.default_output_config().unwrap();
    println!("Default output format: {:?}", config.sample_format());
    let input = host.default_input_device().unwrap();
    let in_config = input.default_input_config().unwrap();
    println!("Default input format: {:?}", in_config.sample_format());
}
