use std::fmt;

#[derive(Debug)]
pub struct HarnessError(pub String);

impl fmt::Display for HarnessError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for HarnessError {}

impl From<String> for HarnessError {
    fn from(value: String) -> Self {
        HarnessError(value)
    }
}

impl From<std::io::Error> for HarnessError {
    fn from(value: std::io::Error) -> Self {
        HarnessError(value.to_string())
    }
}

impl From<symphonia::core::errors::Error> for HarnessError {
    fn from(value: symphonia::core::errors::Error) -> Self {
        HarnessError(value.to_string())
    }
}

#[macro_export]
macro_rules! bail {
    ($($arg:tt)*) => {
        return Err($crate::HarnessError(format!($($arg)*)))
    };
}
