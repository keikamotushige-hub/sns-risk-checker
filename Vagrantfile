# -*- mode: ruby -*-
# vi: set ft=ruby :
# VirtualBox deploy for SNS Risk Checker
# Usage: vagrant up
# App: http://192.168.56.10:5000

Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/jammy64"
  config.vm.hostname = "sns-risk-checker"
  config.vm.network "private_network", ip: "192.168.56.10"
  config.vm.network "forwarded_port", guest: 5000, host: 5000

  config.vm.provider "virtualbox" do |vb|
    vb.name = "sns-risk-checker"
    vb.memory = 2048
    vb.cpus = 2
  end

  config.vm.synced_folder ".", "/opt/sns-risk-checker"

  config.vm.provision "shell", path: "scripts/vagrant-bootstrap.sh", env: {
    "GEMINI_API_KEY" => ENV["GEMINI_API_KEY"].to_s,
    "OWNER_EMAILS" => ENV.fetch("OWNER_EMAILS", "keikamotushige@gmail.com"),
    "AUTH_MODE" => ENV.fetch("AUTH_MODE", "local"),
    "SECRET_KEY" => ENV.fetch("SECRET_KEY", "dev-change-me-in-production"),
  }
end
