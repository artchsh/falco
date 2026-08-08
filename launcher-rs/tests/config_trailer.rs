use falco_stub::config::{append_config, read_config_from_bytes, LauncherConfig};

fn sample() -> LauncherConfig {
    LauncherConfig {
        launcher_name: "server-client-X".into(),
        host: "1.2.3.4".into(),
        username: "root".into(),
        port: 22,
        credential_id: "falco:server-client-X:root@1.2.3.4:22".into(),
        schema_version: 1,
    }
}

#[test]
fn append_then_read_round_trips() {
    let mut bin = b"FAKE-STUB-BINARY-CONTENT".to_vec();
    append_config(&mut bin, &sample());
    let got = read_config_from_bytes(&bin).unwrap();
    assert_eq!(got, sample());
}

#[test]
fn read_without_trailer_errors() {
    let bin = b"just a plain binary".to_vec();
    assert!(read_config_from_bytes(&bin).is_err());
}
