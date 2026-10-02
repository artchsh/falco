use falco_stub::config::{append_config, read_config_from_bytes, LauncherConfig};

fn sample() -> LauncherConfig {
    LauncherConfig {
        launcher_name: "server-client-X".into(),
        host: "1.2.3.4".into(),
        username: "root".into(),
        port: 22,
        credential_id: "falco:server-client-X:root@1.2.3.4:22".into(),
        schema_version: 1,
        auth_method: "password".into(),
        encrypted_private_key: None,
        requires_vpn: false,
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

#[test]
fn legacy_configuration_defaults_and_invalid_versions_fail() {
    let cfg = LauncherConfig::from_json(r#"{"launcher_name":"n","host":"h","username":"u","port":22,"credential_id":"c","schema_version":1}"#).unwrap();
    assert_eq!(serde_json::to_value(&cfg).unwrap()["auth_method"], "password");
    for extra in [r#""schema_version":99"#, r#""schema_version":2,"auth_method":"private_key","encrypted_private_key":"secret""#] {
        let json = format!(r#"{{"launcher_name":"n","host":"h","username":"u","port":22,"credential_id":"c",{extra}}}"#);
        assert!(LauncherConfig::from_json(&json).is_err());
    }
}
