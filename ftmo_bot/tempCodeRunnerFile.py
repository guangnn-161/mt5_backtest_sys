run_id = log_experiment(params=strat_params, metrics=combined_metrics,
                            notes=f"Tự động chạy chiến lược {STRATEGY_NAME}")

    # Tự động tạo thư mục và lưu report vào reports/<STRATEGY_NAME>/
    save_report_and_trades(STRATEGY_NAME, run_id,
                           trade_history, combined_metrics)
    print(
        f"[*] Hoàn tất! Báo cáo, dữ liệu lệnh và Dashboard đã lưu tại: reports/{STRATEGY_NAME}/")


if __name__ == "__main__":
    main()