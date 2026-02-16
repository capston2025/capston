# frozen_string_literal: true

class Gaia < Formula
  include Language::Python::Virtualenv

  desc "Goal-oriented Autonomous Intelligence for Adaptive GUI Testing"
  homepage "https://github.com/capston2025/capston"
  url "https://github.com/capston2025/capston/archive/refs/heads/main.tar.gz"
  # NOTE: tag a release and replace this with the tag tarball + real sha256.
  # Example:
  # url "https://github.com/capston2025/capston/archive/refs/tags/v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "82c89dcf9bf6fc06c2ca6beb56ffdc99751db486f97372776b19c5236b4031aa"
  license "MIT"

  depends_on "python@3.12"

  def install
    venv = virtualenv_create(libexec, Formula["python@3.12"].opt_bin/"python3.12")
    venv.pip_install Pathname.pwd
    venv.pip_install_and_link
  end

  test do
    assert_match "usage", shell_output("#{bin}/gaia --help")
  end
end
